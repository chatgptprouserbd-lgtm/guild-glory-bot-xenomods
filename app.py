# -*- coding: utf-8 -*-
# ============================================================
#   CGU — FREE FIRE 4-BOT TEAM + AUTO CREATE + MOVEMENT
#   + Auto-load accounts + TCP-idle-on-match + All-done restart
#   + FIXED: match-end detection + friend-request accept
#   + AUTO-RESTART: no match 90s (HANYA saat ONLINE/SEARCHING)
# ============================================================
import sys, os, json, time, uuid, random, asyncio, base64, struct, socket
import itertools, traceback, ssl, hmac, hashlib, string, re, subprocess
import httpx
import aiohttp
import aiohttp.web
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception: pass

from google_play_scraper import app as play_scraper
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from protobuf_decoder.protobuf_decoder import Parser
from message_ids import MESSAGE_ID_TO_NAME
import thunderFF_pb2

try:
    import StartMatch_pb2
except Exception:
    StartMatch_pb2 = None

try:
    import reqClan_pb2
    _CLAN_PB_AVAILABLE = True
except Exception:
    _CLAN_PB_AVAILABLE = False

# ==================== CONFIG ====================
WEB_HOST = "0.0.0.0"
WEB_PORT = 25567
ACCOUNTS_FILE = "accounts.json"
TOKEN_CACHE_FILE = "token_cache.json"
DEVICES_FILE = "devices.json"
TOKEN_CACHE_TTL = 1200
INDEX_HTML_PATH = "templates/index.html"

START_MATCH_INTERVAL = 3.0
NEW_MATCH_DELAY = 2.0
MAX_MATCH_DURATION = 1500      # 25 min (real BR is 15-20 min)

# --- Match idle ---
MATCH_IDLE_TIMEOUT  = 35.0     

# --- Auto-restart ---
NO_MATCH_RESTART_ENABLED  = True       
NO_MATCH_RESTART_TIMEOUT  = 90.0       
NO_MATCH_RESTART_GRACE    = 60.0       

_accept_friend_request = True
DEBUG_ALL_PACKETS = False

TEAM_MODE = True
TEAM_SIZE = 4
TEAM_READY_DELAY = 5.0
TEAM_INVITE_WAIT = 2.5
CLAN_ID_DEFAULT = "3105331459"

AES_KEY = bytes([89, 103, 38, 116, 99, 37, 68, 69, 117, 104, 54, 37, 90, 99, 94, 56])
AES_IV  = bytes([54, 111, 121, 90, 68, 114, 50, 50, 69, 51, 121, 99, 104, 106, 77, 37])
KEYSTREAM = bytes([0x30,0x30,0x30,0x32,0x30,0x31,0x37,0x30,0x30,0x30,0x30,0x30,0x32,0x30,0x31,0x37,0x30,0x30,0x30,0x30,0x30,0x32,0x30,0x31,0x37,0x30,0x30,0x30,0x30,0x30,0x32,0x30])
HEX_KEY = "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
API_KEY = HEX_KEY.encode()

REGION_LANG = {"ME":"ar","IND":"hi","ID":"id","VN":"vi","TH":"th","BD":"bn",
               "PK":"ur","TW":"zh","RU":"ru","SAC":"es","BR":"pt","NA":"pt",
               "SG":"pt","US":"pt"}

# ==================== STATE ====================
class BotState:
    def __init__(self):
        self.accounts: Dict[str, dict] = {}
        self.account_credentials: Dict[str, dict] = {}
        self.account_workers: Dict[str, asyncio.Task] = {}
        self.auth_to_game_id: Dict[str, str] = {}
        self.game_to_auth_id: Dict[str, str] = {}
        self.account_token_map: Dict[str, str] = {}
        self.paused: set = set()
        self.writers: Dict[str, list] = {}
        self.logs: List[dict] = []
        self.total_matches = 0
        self.matches_started = 0
        self.total_exp_gained = 0
        self.create_progress: List[str] = []
        self.creating = False
        self.start_time = time.time()
        self._writer_lock = asyncio.Lock()

    def log(self, msg, level="info"):
        try:
            t = datetime.now().strftime("%H:%M:%S")
            self.logs.append({"t": time.time(), "time": t, "message": str(msg), "level": level})
            if len(self.logs) > 500:
                self.logs = self.logs[-500:]
        except Exception:
            pass

    def register_account(self, uid, nickname, region="ME", level=1, exp=0,
                         likes=0, token=None, auth_uid=None):
        uid = str(uid)
        existing = self.accounts.get(uid, {})
        self.accounts[uid] = {
            "uid": uid, "nickname": nickname, "region": region,
            "level": level, "exp": exp, "likes": likes,
            "token": token, "auth_uid": auth_uid,
            "status": "ONLINE",
            "current_exp": exp,
            "initial_exp": existing.get("initial_exp", exp),
            "gained_exp": existing.get("gained_exp", 0),
            "matches": existing.get("matches", 0),
            "active_matches": 0,
            "uptime_seconds": existing.get("uptime_seconds", 0),
            "last_match_time": existing.get("last_match_time", "Running..."),
            "is_paused": False,
        }

    def update_status(self, uid, status, matches=0):
        uid = str(uid)
        if uid in self.accounts:
            self.accounts[uid]["status"] = status
            self.accounts[uid]["active_matches"] = matches
            self.accounts[uid]["is_paused"] = (status == "PAUSED")

    def update_exp(self, uid, exp, level=None):
        uid = str(uid)
        if uid in self.accounts:
            a = self.accounts[uid]
            old = a.get("current_exp", 0)
            if exp > old:
                a["gained_exp"] = a.get("gained_exp", 0) + (exp - old)
                self.total_exp_gained += (exp - old)
            a["current_exp"] = exp
            a["exp"] = exp
            if level: a["level"] = level

    def increment_match(self, uid):
        self.total_matches += 1
        uid = str(uid)
        if uid in self.accounts:
            self.accounts[uid]["matches"] = self.accounts[uid].get("matches", 0) + 1
            self.accounts[uid]["last_match_time"] = datetime.now().strftime("%H:%M:%S")

    def increment_match_started(self):
        self.matches_started += 1

    def register_writer(self, uid, w):
        self.writers.setdefault(str(uid), []).append(w)

    def unregister_writer(self, uid, w):
        try: self.writers.get(str(uid), []).remove(w)
        except Exception: pass

    def close_writers_for_account(self, uid):
        for w in list(self.writers.get(str(uid), [])):
            try: w.close()
            except Exception: pass
        self.writers[str(uid)] = []

    def is_paused(self, uid):
        return str(uid) in self.paused

    def toggle_pause(self, uid, state):
        uid = str(uid)
        if state:
            self.paused.add(uid)
            if uid in self.accounts:
                self.accounts[uid]["is_paused"] = True
                self.accounts[uid]["status"] = "PAUSED"
        else:
            self.paused.discard(uid)
            if uid in self.accounts:
                self.accounts[uid]["is_paused"] = False
                if self.accounts[uid].get("status") == "PAUSED":
                    self.accounts[uid]["status"] = "ONLINE"

bot_state = BotState()

_team_state = {
    "peers": {},
    "leader_uid": None,
    "team_code": None,
    "lock": asyncio.Lock(),
}

_restart_state = {
    "lock": asyncio.Lock(),
    "finished_bots": set(),
    "target": None,
    "restarting": False,
}

async def _mark_bot_match_finished(account_id):
    async with _restart_state["lock"]:
        if _restart_state["restarting"]:
            return
        _restart_state["finished_bots"].add(str(account_id))
        n_done = len(_restart_state["finished_bots"])

        if _restart_state["target"] is None:
            active_ids = {uid for uid, a in bot_state.accounts.items()
                          if a.get("status") not in ("OFFLINE",)}
            _restart_state["target"] = max(1, min(TEAM_SIZE, len(active_ids))) if active_ids else TEAM_SIZE
            print_info(f"[RESTART] Target set to {_restart_state['target']} bots")

        target = _restart_state["target"]
        print_success(f"[RESTART] {account_id} match done → {n_done}/{target}")

        if n_done >= target:
            _restart_state["restarting"] = True
            print_warning(f"[RESTART] ✅ All {target} bots finished! Full process restart in 3s...")
            asyncio.create_task(_schedule_restart())

_no_match_state = {
    "lock": asyncio.Lock(),
    "global_last_match": None,     
    "restart_triggered": False,
}

async def _mark_match_found(account_id=None):
    async with _no_match_state["lock"]:
        _no_match_state["global_last_match"] = time.time()

_ALLOWED_RESTART_STATUSES = {"ONLINE", "SEARCHING"}

def _all_bots_are_online_or_searching() -> bool:
    active_statuses = []
    for uid, acc in bot_state.accounts.items():
        status = str(acc.get("status", "")).upper().strip()
        if status in ("", "OFFLINE"):
            continue
        active_statuses.append(status)

    if not active_statuses:
        return False  

    for st in active_statuses:
        if st not in _ALLOWED_RESTART_STATUSES:
            return False

    return True

async def _no_match_watchdog():
    if not NO_MATCH_RESTART_ENABLED:
        print_info("[NO-MATCH] watchdog disabled")
        return

    await asyncio.sleep(NO_MATCH_RESTART_GRACE)

    async with _no_match_state["lock"]:
        if _no_match_state["global_last_match"] is None:
            _no_match_state["global_last_match"] = time.time()

    print_info(f"[NO-MATCH] watchdog aktif | timeout={NO_MATCH_RESTART_TIMEOUT:.0f}s | grace={NO_MATCH_RESTART_GRACE:.0f}s")

    while True:
        await asyncio.sleep(10.0)
        try:
            if _restart_state["restarting"]:
                return
            if _no_match_state["restart_triggered"]:
                return

            if not _all_bots_are_online_or_searching():
                async with _no_match_state["lock"]:
                    _no_match_state["global_last_match"] = time.time()
                continue

            async with _no_match_state["lock"]:
                last = _no_match_state["global_last_match"] or 0
            elapsed = time.time() - last

            if elapsed >= NO_MATCH_RESTART_TIMEOUT:
                if not _all_bots_are_online_or_searching():
                    async with _no_match_state["lock"]:
                        _no_match_state["global_last_match"] = time.time()
                    continue

                async with _no_match_state["lock"]:
                    if _no_match_state["restart_triggered"]:
                        continue
                    _no_match_state["restart_triggered"] = True

                msg = f"[NO-MATCH] ⚠️ No match for {elapsed:.0f}s → restart!"
                print_warning(msg)
                bot_state.log(msg, "warning")

                async with _restart_state["lock"]:
                    _restart_state["restarting"] = True
                await _schedule_restart()
                return
        except Exception as e:
            print_warning(f"[NO-MATCH] watchdog error: {e}")

async def _schedule_restart():
    await asyncio.sleep(3.0)
    print_warning("[RESTART] 🔄 Executing full process restart...")
    try:
        sys.stdout.flush(); sys.stderr.flush()
    except Exception: pass
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print_error(f"[RESTART] execv failed: {e}")
        try:
            subprocess.Popen([sys.executable] + sys.argv)
            os._exit(0)
        except Exception as e2:
            print_error(f"[RESTART] subprocess fallback failed: {e2}")
            os._exit(1)

# ==================== PRINT HELPERS ====================
class C:
    GREEN='\033[92m'; FAIL='\033[91m'; WARNING='\033[93m'
    CYAN='\033[96m'; MAGENTA='\033[95m'; WHITE='\033[97m'; ENDC='\033[0m'

def print_success(t): print(f"{C.GREEN}[+] {t}{C.ENDC}"); bot_state.log(t, "success")
def print_error(t):   print(f"{C.FAIL}[-] {t}{C.ENDC}");   bot_state.log(t, "error")
def print_warning(t): print(f"{C.WARNING}[!] {t}{C.ENDC}"); bot_state.log(t, "warning")
def print_info(t):    print(f"{C.CYAN}[i] {t}{C.ENDC}");   bot_state.log(t, "info")

GGRE_RAW = bytes.fromhex(
    "47475245010101006d020000b260ee08e1f86b4c57f9c70f86bba26ed5d1436bcf52e142db3249d905eded757764991ca31a8373cdd26eab91b80f3f1f6f262f4f10b895d7b6937ddba30a27197453890e9da49373f736c679b8254e2f8e1623e91084a5fdd5374fe478ff99e010834553fddeb0bec4018d142e49df9bb236675b67e852f92a43586f7a7d4d0d7a197a1d4da32714dab4d069ad53214e2c33b3877420a12459738c4c619cbd815fa878bbd104776bb3e1ac7818d5397b04a666ac57f682763ff2df31bc2f846ddc7904f3dce1bda0c4b01f9698e9166f98c84f92640abe3f6317834a42e5c12573b46842c1a6ea8fe6ca9c006dca48a2087e1f983dfb5692771e4a0b15b337ad669b1d08b40862b176bace5331b49a767375b1a8469012aa70a67b55fe71b72478201d0b3ac7269c064c960361e92ee7ba8a42cc6b582bf9b965fb388fa9172ad44c4b073ac23c02080a6bcd106b691ffdf4c7cf71e42f2063fcf196e9bddc5e85be1fe5048eb1b31b460efbb46d76195eef9904c4cba326f2e17ff51fec17dd965aa06dadd4ab07d6966e4a7c38e8afd66dfde56c872bb87516a8f7313c797e4d80e5ad4a5f7afad95c1e0449254adae052e71a3fb98399f93ab30848e0d23252dd45e6fd41bc5fa7303dfb846a8fd713a0032a0b0ae96dfba1bbe41d20abd8099e2cb7fccc329d25bd153029139ef05d090a5093ea557693c0a8d491395b8a23ca844b3887dd5dfc29ac06f4b9be87883a793211b973465e2644c4f5de02b8ab01571401203fc2740423826858cc6da0194c195d27aac4ec4b9d23d506c1501d06440aca3fc180926d75d4a004d3dff21eb4ea1e8c86e6c9627248eff953e1d192d5c8efc006a7e512388ef2ebdb72ee3ad42d719f28e8f994e12ebf4c79f2ffe3abd7408ccd2236a7b89b2606247a732c10c4"
)

def _encode_proto_std(fields):
    out = b""
    for f in sorted(fields.keys()):
        v = fields[f]
        if isinstance(v, bool):
            out += encode_varint((f << 3) | 0) + encode_varint(int(v))
        elif isinstance(v, int):
            out += encode_varint((f << 3) | 0) + encode_varint(v)
        elif isinstance(v, (str, bytes)):
            if isinstance(v, str): v = v.encode()
            out += encode_varint((f << 3) | 2) + encode_varint(len(v)) + v
    return out

# ==================== DEVICE & DNS ====================
def _generate_new_device() -> dict:
    device_list = [
        ("Samsung","SM-G998B","Adreno (TM) 660","Android OS 12 / API-31"),
        ("Xiaomi","2201122G","Adreno (TM) 730","Android OS 13 / API-33"),
        ("Realme","RMX3700","Mali-G710","Android OS 14 / API-34"),
        ("OnePlus","CPH2451","Adreno (TM) 740","Android OS 13 / API-33"),
        ("OPPO","CPH2611","Adreno (TM) 720","Android OS 14 / API-34"),
    ]
    brand, model, gpu, os_ver = random.choice(device_list)
    return {"unique_device_id":f"Google|{uuid.uuid4()}","brand":brand,"model":model,
            "gpu_renderer":gpu,"system_software":os_ver,
            "screen_width":random.choice([1080,1440,720]),
            "screen_height":random.choice([2400,3200,1600]),
            "screen_dpi":str(random.randint(300,420)),
            "memory":random.randint(2800,6500)}

def get_device_for_account(acc_key: str) -> dict:
    devices = {}
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE,"r",encoding="utf-8") as f:
                devices = json.load(f)
                if not isinstance(devices, dict): devices = {}
        except Exception: devices = {}
    if acc_key in devices: return devices[acc_key]
    nd = _generate_new_device()
    devices[acc_key] = nd
    try:
        with open(DEVICES_FILE,"w",encoding="utf-8") as f:
            json.dump(devices, f, indent=2)
    except Exception: pass
    return nd

_DNS_CACHE: Dict[str, Tuple[str,float]] = {}
_DNS_CACHE_TTL = 300.0

async def resolve_host_cloudflare(hostname: str) -> str:
    if not hostname: return hostname
    parts = hostname.split('.')
    if len(parts)==4 and all(p.isdigit() for p in parts): return hostname
    now = time.time()
    if hostname in _DNS_CACHE:
        ip, exp = _DNS_CACHE[hostname]
        if now < exp: return ip
    try:
        loop = asyncio.get_running_loop()
        info = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
        if info:
            ip = info[0][4][0]
            _DNS_CACHE[hostname] = (ip, now + _DNS_CACHE_TTL)
            return ip
    except Exception: pass
    return hostname

def optimize_tcp_socket(sock):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
    except Exception: pass

async def safe_close_writer(writer):
    if not writer: return
    try:
        if not writer.is_closing(): writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=1.5)
    except Exception: pass

def optimize_udp_socket(sock):
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
    except Exception: pass

# ==================== HTTP CLIENT ====================
client = httpx.AsyncClient(verify=False, timeout=15.0,
    limits=httpx.Limits(max_connections=300, max_keepalive_connections=150))
_LOGIN_SEMAPHORE = asyncio.Semaphore(4)

# ==================== CRYPTO / PROTOBUF ====================
CRC7_TABLE = bytes([
    0,9,18,27,36,45,54,63,72,65,90,83,108,101,126,119,
    25,16,11,2,61,52,47,38,81,88,67,74,117,124,103,110,
    50,59,32,41,22,31,4,13,122,115,104,97,94,87,76,69,
    43,34,57,48,15,6,29,20,99,106,113,120,71,78,85,92,
    100,109,118,127,64,73,82,91,44,37,62,55,8,1,26,19,
    125,116,111,102,89,80,75,66,53,60,39,46,17,24,3,10,
    86,95,68,77,114,123,96,105,30,23,12,5,58,51,40,33,
    79,70,93,84,107,98,121,112,7,14,21,28,35,42,49,56,
    65,72,83,90,101,108,119,126,9,0,27,18,45,36,63,54,
    88,81,74,67,124,117,110,103,16,25,2,11,52,61,38,47,
    115,122,97,104,87,94,69,76,59,50,41,32,31,22,13,4,
    106,99,120,113,78,71,92,85,34,43,48,57,6,15,20,29,
    37,44,55,62,1,8,19,26,109,100,127,118,73,64,91,82,
    60,53,46,39,24,17,10,3,116,125,102,111,80,89,66,75,
    23,30,5,12,51,58,33,40,95,86,77,68,123,114,105,96,
    14,7,28,21,42,35,56,49,70,79,84,93,98,107,112,121,
])
_DELTA = 0x9E3779B9
_ROUNDS = 16
_FIELD_SIZES = {0:1,1:2,2:2,3:1,4:2}
_FIELD_NAMES = {0:"sendOption",1:"cmd",2:"orderId",3:"flags",4:"length"}

def crc7_sync(data: bytes) -> int:
    c = 0
    for b in data:
        c = CRC7_TABLE[((2*(c & 0xFF)) ^ (b & 0xFF)) & 0xFF] & 0x7F
    return c & 0x7F

def encode_varint(n: int) -> bytes:
    out = bytearray()
    if n < 0: n &= 0xFFFFFFFFFFFFFFFF
    while True:
        b = n & 0x7F; n >>= 7
        if n: b |= 0x80
        out.append(b)
        if not n: break
    return bytes(out)

def _pb_varint(fn, val): return encode_varint((fn<<3)|0) + encode_varint(val)
def _pb_length(fn, val):
    enc = val.encode('utf-8') if isinstance(val,str) else val
    return encode_varint((fn<<3)|2) + encode_varint(len(enc)) + enc

def create_proto(fields) -> bytes:
    packet = bytearray()
    for field, value in fields.items():
        if isinstance(field, str): field = int(field)
        if isinstance(value, dict):
            packet.extend(_pb_length(field, bytes(create_proto(value))))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    packet.extend(_pb_length(field, bytes(create_proto(item))))
                elif isinstance(item, str):
                    packet.extend(_pb_length(field, item))
                elif isinstance(item, (bytes, bytearray)):
                    packet.extend(_pb_length(field, bytes(item)))
                elif isinstance(item, bool):
                    packet.extend(_pb_varint(field, 1 if item else 0))
                elif isinstance(item, int):
                    packet.extend(_pb_varint(field, item))
        elif isinstance(value, bool):
            packet.extend(_pb_varint(field, 1 if value else 0))
        elif isinstance(value, int):
            packet.extend(_pb_varint(field, value))
        elif isinstance(value, str):
            packet.extend(_pb_length(field, value))
        elif isinstance(value, (bytes, bytearray)):
            packet.extend(_pb_length(field, bytes(value)))
    return bytes(packet)

def encrypt_aes(hex_data: str) -> str:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    return cipher.encrypt(pad(bytes.fromhex(hex_data), AES.block_size)).hex()

def decode_varint(data, offset):
    result = 0; shift = 0
    while offset < len(data):
        byte = data[offset]
        result |= (byte & 0x7F) << shift
        offset += 1
        if not (byte & 0x80): return result, offset
        shift += 7
    return None, offset

def decode_protobuf_2(data):
    result = {}; offset = 0
    data_len = len(data)
    while offset < data_len:
        header, offset = decode_varint(data, offset)
        if header is None: break
        fn = header >> 3; wt = header & 0x7
        if wt == 0:
            v, offset = decode_varint(data, offset)
            if v is not None: result[fn] = v
        elif wt == 2:
            length, offset = decode_varint(data, offset)
            if length is None: break
            value = data[offset:offset+length]; offset += length
            try:
                s = value.decode('utf-8')
                result[fn] = s if s else value.hex()
            except UnicodeDecodeError:
                nested = decode_protobuf_2(value)
                result[fn] = nested if nested else value.hex()
        elif wt == 1: offset += 8
        elif wt == 3: offset += 4
        else: break
    return result

def extract_raw_fields(data: bytes) -> dict:
    result = {}; offset = 0; data_len = len(data)
    while offset < data_len:
        header, offset = decode_varint(data, offset)
        if header is None: break
        fn = header >> 3; wt = header & 0x7
        if wt == 0:
            v, offset = decode_varint(data, offset)
            if v is not None: result[fn] = v
        elif wt == 2:
            length, offset = decode_varint(data, offset)
            if length is None: break
            if offset + length > data_len: break
            result[fn] = bytes(data[offset:offset+length]); offset += length
        elif wt == 1: offset += 8
        elif wt == 3: offset += 4
        else: break
    return result

def _parse_results_sync(results):
    out = {}
    for r in results:
        fd = {"wire_type": r.wire_type}
        if r.wire_type == "varint": fd["data"] = r.data
        elif r.wire_type in ("string","bytes"): fd["data"] = r.data
        elif r.wire_type == "length_delimited":
            if hasattr(r.data, "results"): fd["data"] = _parse_results_sync(r.data.results)
            elif isinstance(r.data, list): fd["data"] = _parse_results_sync(r.data)
            else: fd["data"] = str(r.data)
        out[str(r.field)] = fd
    return out

def decode_protobuf_dict(data: bytes) -> dict:
    try:
        parsed = Parser().parse(data)
        return _parse_results_sync(parsed)
    except Exception:
        return {}

async def decode_protobuf_async(data):
    try:
        parsed = Parser().parse(data)
        return json.dumps(_parse_results_sync(parsed))
    except Exception:
        return "{}"

# ==================== ZIGZAG / TEAM / CLAN ====================
def zigzag_encode(n: int) -> bytes:
    z = (n << 1) if n >= 0 else ((-n) << 1) - 1
    return encode_varint(z)

def uleb_encode(n: int) -> bytes:
    return encode_varint(n)

def create_packet(Pk, N, K, V):
    if isinstance(K, str): K = bytes.fromhex(K) if len(K)==32 else K.encode()
    if isinstance(V, str): V = bytes.fromhex(V) if len(V)==32 else V.encode()
    PkEnc = AES.new(K, AES.MODE_CBC, V).encrypt(pad(bytes.fromhex(Pk),16)).hex()
    L = len(PkEnc)//2
    hexlen = hex(L)[2:]
    if len(hexlen)%2 != 0: hexlen = '0'+hexlen
    total_pad = 12 - len(N) - len(hexlen)
    if total_pad < 0: total_pad = 0
    return bytes.fromhex(N + ('0'*total_pad) + hexlen + PkEnc)

def build_movement_packet_2001(key_bytes, msg_key, player_state, x, y, z,
                                tick, counter, move_state, account_uid,
                                room_code, phase="arena"):
    if phase == "game":
        TAIL = (b"\x00"*7 + b"\x93\xca\xd5\xf9\x0b" + b"\x8e\xfe\xfc\xf7\x0b"
                + b"\xf4\xe0\xfe\xf7\x03" + b"\x00\x00")
    else:
        TAIL = (b"\x00"*7 + b"\xbd\xb6\xe1\xf3\x03" + b"\x00"
                + b"\xc4\xb6\xe1\xfb\x0b" + b"\x00\x00")
    body = bytearray()
    body += uleb_encode(int(account_uid))
    body += uleb_encode(int(player_state))
    body += zigzag_encode(int(x))
    body += zigzag_encode(int(y))
    body += zigzag_encode(int(z))
    body += b"\x00"
    body += uleb_encode(int(move_state))
    body += TAIL
    body += uleb_encode(int(tick))
    body += b"\x00\x00\x00"
    body += uleb_encode(int(counter))
    body = bytes(body)
    k = key_bytes[0]
    v80 = ((k << 8) | k) & 0xFFFF
    h = bytearray()
    h.append(msg_key)
    h.append(0x00)
    h.append(0x00 ^ k)
    h += ((2001 ^ v80) & 0xFFFF).to_bytes(2, "little")
    h += (len(body) ^ v80).to_bytes(2, "little")
    h.append(0x00 ^ k)
    h[1] = crc7_sync(bytes(h[2:]) + body)
    return bytes(h) + body

def create_team(key, iv, game_version="1.132.9"):
    fields = {
        1: 1, 2: {2: bytes.fromhex("01"), 3:1, 4:3, 5:"en",
            8: {1:"IDC3",2:123,3:"ME"}, 9:1,
            10: bytes.fromhex("01090a0b1219202729"), 11:2, 13:1,
            14: {1:"08FBA33B4BABE2790205390333330000009700050092005F72697EB2134DCBB2467625142201040e38e848dc0e748c3f6aa04d81000000ff1113090dcacfa16d",
                 2:478, 3:"7e585d55", 4:"y[Z]", 6:12, 7:"15686677735a7506101c",
                 8:str(game_version), 9:3, 10:1, 11:"03626253513637754174"},
            19: 329, 21: bytes.fromhex("374f521955"), 24:{1:21},
            27:"a_6534489873065906362"}}
    return create_packet(create_proto(fields).hex(), "0515", key, iv)

def team_fresh(account_id, key, iv):
    return create_packet(create_proto({1:72, 2:{1:int(account_id)}}).hex(), "0515", key, iv)

def invite_player_in_team(target_uid, key, iv):
    return create_packet(create_proto({1:2, 2:{1:int(target_uid),2:"ME",4:1}}).hex(), "0515", key, iv)

def accept_team_invite(target_uid, group_id, key, iv):
    fields = {1:4, 2:{1:int(target_uid), 3:int(target_uid),
        4: bytes.fromhex("0107090a0b120f19202729"), 8:1,
        9: {1:"08FBA33B0522052002041B0222220007000C00010004000547E265AE1080779546762514110104076fa2e8770e748c3f6a68ed75000000ff08080500cacfa16d",
            2:235, 3:{14:80,11:86}, 4:"{Y\\R", 6:13, 7:{2:4},
            8:"1.132.9", 9:2, 10:1, 11:"0362625351"},
        10: str(group_id),
        11: {"1":"IDC3","2":107,"3":"ME"}, 13:"en", 16:"374f5219",
        20:{1:21}, 23:"a_6534489873065906362",
        24:"https://dl-sg-production.freefiremobile.com/",
        27:{1:2, 2:4}}}
    return create_packet(create_proto(fields).hex(), "0515", key, iv)

def start_match_team(account_id, key, iv, region="ME"):
    fields = {1:9, 2:{1:int(account_id), 7:{1:"IDC3", 2:410, 3:region}}}
    return create_packet(create_proto(fields).hex(), "0515", key, iv)

def ready_for_game(uid, key, iv):
    fields = {1: 15, 2: {1: uid, 2: 1}}
    return create_packet(create_proto(fields).hex(), "0519", key, iv)

def _extract_key_iv(res_json):
    if "__RAW_HEX__" in res_json and "__OFFSET__" in res_json:
        try:
            raw = bytes.fromhex(res_json["__RAW_HEX__"])
            off = int(res_json["__OFFSET__"])
            rf = extract_raw_fields(raw[off:])
            for kf, vf in [(22,23),(24,25),(6,7)]:
                k = rf.get(kf); v = rf.get(vf)
                if isinstance(k,(bytes,bytearray)) and len(k)==16 and \
                   isinstance(v,(bytes,bytearray)) and len(v)==16:
                    return bytes(k), bytes(v)
        except Exception: pass
    found = {}
    for k, v in res_json.items():
        if str(k).startswith("__"): continue
        if not isinstance(v, dict): continue
        data = v.get("data")
        if data is None: continue
        raw = None
        if isinstance(data, (bytes,bytearray)) and len(data)==16: raw = bytes(data)
        elif isinstance(data, str) and len(data)==32:
            try:
                c = bytes.fromhex(data)
                if len(c)==16: raw = c
            except Exception: pass
        if raw and len(raw)==16:
            try: found[int(k)] = raw
            except Exception: pass
    if len(found) < 2: return None, None
    for kf, vf in [(22,23),(24,25),(6,7)]:
        if kf in found and vf in found: return found[kf], found[vf]
    sk = sorted(found.keys())
    return found[sk[0]], found[sk[1]]

async def aes_encrypt(payload, key, iv):
    return AES.new(key, AES.MODE_CBC, iv).encrypt(pad(payload, AES.block_size))

def get_app_version():
    try: return play_scraper('com.dts.freefireth').get('version')
    except Exception: return "1.132.9"

async def version_config(region="ME"):
    app_version = get_app_version()
    lang = REGION_LANG.get(region.upper(), "en")
    url = (f"https://version.ggwhitehawk.com/live/ver.php?version={app_version}"
           f"&lang={lang}&device=android&channel=android&appstore=googleplay"
           f"&region={region.upper()}&whitelist_version=1.3.0&whitelist_sp_version=1.0.0")
    try:
        r = await client.get(url)
        j = r.json()
        server_url = j.get("server_url")
        remote_version = j.get("remote_version")
        latest_release_version = j.get("latest_release_version")
        gops = [u.strip() for u in j.get("gop_url","").split(";") if u.strip()]
        gop_1 = gops[0] if gops else "https://ffmconnect.ppmainecoonghj.com"
        if not server_url: return None
        return latest_release_version, remote_version, server_url, gop_1, app_version
    except Exception as e:
        print_error(f"[VERCONFIG] {e}"); return None

async def get_access_token(uid, password):
    url = "https://100067.connect.garena.com/oauth/guest/token/grant"
    hdrs = {"Host":"100067.connect.garena.com",
        "User-Agent":"Dalvik/2.1.0 (Linux; U; Android 12; SM-G998B)",
        "Content-Type":"application/x-www-form-urlencoded",
        "Accept-Encoding":"gzip, deflate, br","Connection":"close"}
    data = {"uid":str(uid),"password":password,"response_type":"token","client_type":"2",
        "client_secret":HEX_KEY,"client_id":"100067"}
    for _ in range(4):
        try:
            r = await client.post(url, headers=hdrs, data=data)
            if r.status_code == 200:
                j = r.json()
                oid = j.get("open_id"); at = j.get("access_token"); pf = j.get("platform",4)
                if oid and at: return oid, at, pf
        except Exception: pass
        await asyncio.sleep(0.7)
    return None

def generate_signature(payload_str):
    return hmac.new(API_KEY, payload_str.encode(), hashlib.sha256).hexdigest()

async def register_account(password, gop_url, app_version):
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    rp = {"app_id":100067,"client_type":2,"password":password,"source":2}
    payload = json.dumps(rp, separators=(',',':'))
    sig = generate_signature(payload)
    headers = {
        "User-Agent": f"GarenaMSDK/4.0.44(SM-E135F ;Android 14;en;GB;app {app_version} 2019118525;)",
        "Authorization": f"Signature {sig}",
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8"}
    try:
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.post(f"{gop_url}/api/v2/oauth/guest:register",
                              headers=headers, data=payload,
                              timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = json.loads(await resp.text())
                if data.get("code") == 0: return data["data"]["uid"]
    except Exception as e:
        print_error(f"[REGISTER] {e}")
    return None

async def grant_token(uid, password, gop_url, app_version):
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    gp = {"client_id":100067,"client_secret":HEX_KEY,"client_type":2,
          "device_id":f"02-{uuid.uuid4()}","password":password,
          "response_type":"token","uid":int(uid)}
    payload = json.dumps(gp, separators=(',',':'))
    sig = generate_signature(payload)
    headers = {
        "User-Agent": f"GarenaMSDK/4.0.44(SM-E135F ;Android 14;en;GB;app {app_version} 2019118525;)",
        "Authorization": f"Signature {sig}",
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8"}
    try:
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.post(f"{gop_url}/api/v2/oauth/guest/token:grant",
                              headers=headers, data=payload,
                              timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = json.loads(await resp.text())
                if data.get("code") == 0: return data["data"]
    except Exception as e:
        print_error(f"[GRANT] {e}")
    return None

async def get_payload(access_token, open_id, platform, version, lang_code="en"):
    device_id = f"Google|{uuid.uuid4()}"
    major_login = {
        "3": str(datetime.now())[:-7], "4": "free fire", "5": 1,
        "7": f"{version}",
        "8": "Android OS 14 / API-34 (UP1A.231005.007/E135FXXSEEZE2)",
        "9": "Handheld", "11": "CarrierDataNetwork",
        "12": 1672, "13": 750, "14": "338",
        "15": "ARMv7 VFPv3 NEON | 2002 | 8",
        "16": 3702, "17": "Mali-G52",
        "18": "OpenGL ES 3.2 v1.r38p1-01bet0-mbs2v41_0.6d20ec041e51b2f2d25dfc265586ebe8",
        "19": device_id, "20": "104.28.197.151", "21": lang_code,
        "22": f"{open_id}", "23": f"{platform}", "24": "Handheld",
        "25": "samsung SM-E135F", "26": "ME",
        "29": f"{access_token}", "30": 1, "42": "Cellular",
        "57": "7428b253defc164018c604a1ebbfebdf",
        "60": 52037, "61": 5355, "62": 3723, "63": 3,
        "64": 5483, "65": 52037, "66": 5483, "67": 52037,
        "73": 2,
        "74": "/data/app/~~yACsV8QOk9OexgajWQP30A==/com.dts.freefireth-Bi_wPVrrhYM2nYncc29q7Q==/lib/arm",
        "76": 1,
        "77": "b8e0cd5e295eee42f5860d3c86e483dd|/data/app/~~yACsV8QOk9OexgajWQP30A==/com.dts.freefireth-Bi_wPVrrhYM2nYncc29q7Q==/base.apk",
        "78": 3, "79": 1, "81": "32", "83": "2019121229",
        "86": "OpenGLES2", "87": 8191, "88": int(platform),
        "92": 16091, "93": "android",
        "94": "KqsHT3Yeqo3RLRl8efNiL3aL/1SHwlSrtK0DwNAqdkNBQz70TeVy+icaIwuR3UK4b65Uf3IG5xBFEv0uu+jQZI+rOYCmNCsXjJT4QcZaYgoaaUGG",
        "95": 111107,
        "96": "{\"cur_rate\":null,\"support_etc2\":false}",
        "97": 1, "98": 1,
        "99": f"{platform}", "100": f"{platform}",
        "102": "4503454457550c0766",
        "104": 53792, "105": 1,
        "106": "https://dl-bs.ggpolarbear.com/live/ABHotUpdates/|https://core-bs.ggpolarbear.com/live/ABHotUpdates/|6b2078db9d22dd98f8e9386a39af8462",
        "107": "c8e41b7a93f02d56e1a94c7b8203f5d1",
    }
    get_login_data = dict(major_login)
    get_login_data["90"] = "Khulna"
    get_login_data["91"] = "D"
    get_login_data["99"] = "0"
    get_login_data["103"] = 1
    proto1 = create_proto(major_login).hex()
    proto2 = create_proto(get_login_data).hex()
    return bytes.fromhex(encrypt_aes(proto1)), bytes.fromhex(encrypt_aes(proto2))

async def major_login(payload, login_url, release_version):
    url = f"{login_url.rstrip('/')}/MajorLogin"
    req_headers = {
        "Accept": "*/*", "Authorization": "Bearer ",
        "Content-Type": "application/x-www-form-urlencoded",
        "ReleaseVersion": f"{release_version}",
        "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
        "X-GA": "v1 1", "X-GA-SV": str(int(time.time())),
        "X-Unity-Version": "2018.4.12f1",
    }
    try:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(url, headers=req_headers, data=payload, ssl=ssl_context) as response:
                response_content = await response.read()
                if response.status != 200 or len(response_content) < 200:
                    return None

                best = None; best_score = -1; best_off = 0
                offsets_to_try = [64, 0, 48, 32, 80, 16, 128, 8, 96, 60, 56, 40, 24, 20, 12, 4]
                for offset in offsets_to_try:
                    if offset >= len(response_content): continue
                    cb = response_content[offset:]
                    cand = None
                    try: cand = decode_protobuf_dict(cb)
                    except Exception: cand = None
                    if not cand:
                        try:
                            c2 = decode_protobuf_2(cb)
                            if c2: cand = {str(k): {"data": v} for k, v in c2.items()}
                        except Exception: cand = None
                    if not cand: continue
                    keys = set(cand.keys())
                    has_jwt = ("8" in keys) or (8 in keys)
                    has_url = ("10" in keys) or (10 in keys)
                    score = len(cand) + (100 if has_jwt else 0) + (50 if has_url else 0)
                    if score > best_score:
                        best = cand; best_score = score; best_off = offset
                    if has_jwt and has_url: break
                if not best or (("8" not in best) and (8 not in best)):
                    return None
                best["__RAW_HEX__"] = response_content.hex()
                best["__OFFSET__"] = best_off
                return best
    except Exception as e:
        return None

async def get_login_data(payload, jwt_token, server_url, release_version):
    url = f"{server_url.rstrip('/')}/GetLoginData"
    hdrs = {"Accept":"*/*","Authorization":f"Bearer {jwt_token}",
        "Content-Type":"application/x-www-form-urlencoded",
        "ReleaseVersion":f"{release_version}",
        "User-Agent":"UnityPlayer/2018.4.12f1","X-GA":"v1 1",
        "X-GA-SV":str(int(time.time())),"X-Unity-Version":"2018.4.12f1"}
    try:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.post(url, headers=hdrs, data=payload, ssl=ssl_ctx) as r:
                if r.status != 200: return None
                content = await r.read()
                for off in [0,64,48,32,80,16,128,8,96]:
                    if off >= len(content): continue
                    try:
                        c = decode_protobuf_dict(content[off:])
                        if c and len(c) >= 3: return c
                    except Exception: pass
        return None
    except Exception: return None

async def get_player_personal_show(target_uid, jwt_token, server_url, release_version):
    url = f"{server_url}/GetPlayerPersonalShow"
    payload = bytes.fromhex(encrypt_aes(create_proto({"1":int(target_uid)}).hex()))
    hdrs = {"authorization":f"Bearer {jwt_token}",
        "host":server_url.replace("https://","").split("/")[0],
        "x-ga":"v1 1","content-type":"application/x-www-form-urlencoded",
        "releaseversion":f"{release_version}",
        "user-agent":"UnityPlayer/2022.3.47f1","accept":"*/*",
        "x-unity-version":"2022.3.47f1"}
    try:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.post(url, headers=hdrs, data=payload, ssl=ssl_ctx) as r:
                if r.status != 200: return None
                return decode_protobuf_2(await r.read())
    except Exception: return None

# ==================== CLAN FUNCTION ====================
def _get_region_from_jwt(jwt_token):
    try:
        p = jwt_token.split('.')[1]
        p += '=' * (-len(p) % 4)
        d = json.loads(base64.urlsafe_b64decode(p))
        return (d.get('lock_region') or d.get('noti_region') or 'BD').upper()
    except Exception: return "ME"

def _get_region_url(region):
    region = region.upper()
    if region == "IND": return "https://client.ind.freefiremobile.com"
    if region in ["BR","US","NA"]: return "https://client.us.freefiremobile.com"
    return "https://clientbp.ggpolarbear.com"

def _create_clan_payload(clan_id):
    if not _CLAN_PB_AVAILABLE: return None
    try:
        msg = reqClan_pb2.MyMessage()
        msg.field_1 = int(clan_id)
        raw = msg.SerializeToString()
        return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(raw, AES.block_size))
    except Exception as e:
        print_error(f"[CLAN] payload: {e}"); return None

async def join_clan(jwt_token, clan_id):
    if not _CLAN_PB_AVAILABLE:
        print_warning("[CLAN] reqClan_pb2 not available"); return False
    try:
        region = _get_region_from_jwt(jwt_token)
        base_url = _get_region_url(region)
        url = base_url.rstrip("/") + "/RequestJoinClan"
        host = base_url.replace("https://","")
        payload = _create_clan_payload(clan_id)
        if not payload: return False
        hdrs = {"Authorization":f"Bearer {jwt_token}",
            "X-Unity-Version":"2018.4.11f1","X-GA":"v1 1","ReleaseVersion":"OB55",
            "Content-Type":"application/octet-stream","Host":host,
            "Connection":"Keep-Alive","Accept-Encoding":"gzip"}
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(url, headers=hdrs, data=payload, ssl=ssl_ctx) as r:
                if r.status == 200:
                    print_success(f"[CLAN] ✅ Join sent for Clan {clan_id}"); return True
                print_warning(f"[CLAN] Join status {r.status}"); return False
    except Exception as e:
        print_error(f"[CLAN] {e}"); return False

# ==================== TCP / UDP ====================
async def build_tcp_startup_packet(account_id, token, server_time, key, iv, region="ID", typ='OnLine'):
    uid_hex = f"{int(account_id):016x}"
    ts_hex = f"{int(server_time):08x}"
    enc = (await aes_encrypt(token.encode(), key, iv)).hex()
    enc_len = f"{len(enc)//2:08x}"
    reg = str(region).upper() if region else "ID"
    if typ == 'OnLine':
        pfx = '7219' if reg == 'BD' else ('7214' if reg == 'IND' else '7215')
        return f"{pfx}{uid_hex}{ts_hex}00000000{enc_len}{enc}"
    pfx = '8119' if reg == 'BD' else ('8114' if reg == 'IND' else '8115')
    return f"{pfx}{uid_hex}{ts_hex}{enc_len}{enc}"

async def send_keep_alive(region="ID"):
    reg = str(region).upper() if region else "ID"
    return bytes.fromhex("0219" if reg == "ID" else ("0214" if reg == "IND" else "0215"))

# ==================== CACHE ====================
_token_cache_memo: Dict[str, Any] = {}
_token_cache_memo_time: float = 0.0

def _json_serializer(obj):
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes_hex__": bytes(obj).hex()}
    raise TypeError(f"Type {type(obj)} not serializable")

def _json_deserializer(obj):
    if isinstance(obj, dict):
        if "__bytes_hex__" in obj and len(obj) == 1:
            try: return bytes.fromhex(obj["__bytes_hex__"])
            except Exception: return b""
        return {k: _json_deserializer(v) for k, v in obj.items()}
    if isinstance(obj, list): return [_json_deserializer(x) for x in obj]
    return obj

def _load_token_cache():
    if not os.path.exists(TOKEN_CACHE_FILE): return {}
    try:
        with open(TOKEN_CACHE_FILE,"r",encoding="utf-8") as f: content = f.read().strip()
        if not content: return {}
        return _json_deserializer(json.loads(content))
    except Exception: return {}

def _save_token_cache(cache):
    try:
        with open(TOKEN_CACHE_FILE,"w",encoding="utf-8") as f:
            json.dump(cache, f, indent=2, default=_json_serializer)
    except Exception: pass

def cache_get(uid):
    cache = _load_token_cache()
    e = cache.get(str(uid))
    if not e: return None
    if time.time() - e.get("cached_at",0) > TOKEN_CACHE_TTL: return None
    return e

def cache_set(uid, data):
    cache = _load_token_cache()
    e = dict(data); e["cached_at"] = time.time()
    cache[str(uid)] = e; _save_token_cache(cache)

def cache_invalidate(uid):
    cache = _load_token_cache()
    if str(uid) in cache:
        del cache[str(uid)]; _save_token_cache(cache)

# ==================== WORKER LOOP ====================
async def refresh_account_profile(ad_or_uid):
    try:
        if isinstance(ad_or_uid, str):
            uid = str(ad_or_uid); ad = bot_state.account_credentials.get(uid)
        else:
            ad = ad_or_uid; uid = str(ad.get('account_id'))
        if not ad: return
        token = ad.get('token'); acc_id = ad.get('account_id')
        srv = ad.get('server_url'); rel = ad.get('release_version')
        if not (token and acc_id and srv and rel): return
        data = await get_player_personal_show(int(acc_id), token, srv, rel)
        if not data: return
        pd = data.get(1, {}) if isinstance(data.get(1), dict) else {}
        nickname = pd.get(3); lvl = pd.get(6); exp = pd.get(7)
        if exp is None: return
        exp = int(exp); lvl = int(lvl) if lvl else 1
        aid = str(acc_id)
        if aid in bot_state.accounts:
            bot_state.update_exp(aid, exp, lvl)
            if nickname: bot_state.accounts[aid]["nickname"] = nickname
    except Exception: pass

async def process_account_uid_pass(uid, password, region_override=None):
    cached = cache_get(uid)
    if cached:
        aid = str(cached['account_id'])
        nick = cached.get('nickname', f"Player_{aid}")
        bot_state.register_account(aid, nick, cached.get('region','BD'),
            cached.get('level', 1), cached.get('exp', 0), auth_uid=str(uid))
        bot_state.account_credentials[aid] = cached
        return cached
    try:
        async with _LOGIN_SEMAPHORE:
            vc = await version_config(region_override or "ID")
            if not vc: return None
            release_version, client_version, server_url, gop_1, app_version = vc
            tg = await get_access_token(uid, password)
            if not tg: return None
            open_id, access_token, platform = tg

            major_payload, login_payload = await get_payload(
                access_token, open_id, platform, client_version)

            res_json = await major_login(major_payload, server_url, release_version)
            if not res_json: return None

            def _gd(k):
                v = res_json.get(str(k))
                if isinstance(v, dict) and "data" in v: return v["data"]
                return v

            acc_id_raw = _gd(1); region_raw = _gd(2) or "ID"
            jwt_token = _gd(8); server_url_raw = _gd(10); server_time = _gd(21)
            aes_ak, iv_i = _extract_key_iv(res_json)
            if not jwt_token or not server_url_raw or not aes_ak: return None

            ld = await get_login_data(login_payload, jwt_token, server_url_raw, release_version)

        def _gf(f, d=None):
            v = (ld or {}).get(str(f), {}).get("data")
            return v if v is not None else d

        level = int(_gf(6,1) or 1); exp = int(_gf(7,0) or 0)
        nickname = _gf(4, f"Player_{acc_id_raw}") or f"Player_{acc_id_raw}"
        region = region_raw or "ID"
        acc_id = str(acc_id_raw)

        bot_state.register_account(acc_id, nickname, region, level, exp, auth_uid=str(uid))
        print_success(f"[✓] Logged in: {acc_id} | {nickname}")

        ad = {'account_id':acc_id_raw,'nickname':nickname,'region':region,
            'level':level,'exp':exp,'open_id':open_id,'access_token':access_token,
            'token':jwt_token,'server_time':server_time,'aes_ak':aes_ak,'iv_i':iv_i,
            'functional_addrs':_gf(14),'informational_addrs':_gf(32),
            'release_version':release_version,'client_version':client_version,
            'server_url':server_url_raw,'auth_type':'guest','auth_uid':uid,'auth_password':password}
        bot_state.account_credentials[acc_id] = ad
        cache_set(uid, ad); return ad
    except Exception as e:
        print_error(f"Login error: {e}"); return None

async def account_loop_guest(uid, password, region_override=None, clan_override=None):
    uid_str = str(uid)
    bot_state.account_workers[uid_str] = asyncio.current_task()
    while True:
        try:
            bot_state.update_status(uid_str, "CONNECTING")
            ad = await process_account_uid_pass(uid_str, password, region_override)
            if not ad:
                await asyncio.sleep(15); continue
            acc_id = str(ad['account_id'])
            bot_state.auth_to_game_id[uid_str] = acc_id
            if clan_override:
                asyncio.create_task(join_clan(ad['token'], clan_override))
            bot_state.update_status(uid_str, "ONLINE")
            while True:
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            bot_state.update_status(uid_str, "OFFLINE")
            break
        except Exception as e:
            await asyncio.sleep(10)

async def _autoload_accounts():
    if not os.path.exists(ACCOUNTS_FILE): return
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            accounts = json.load(f) or []
    except Exception: return
    for a in accounts:
        if isinstance(a, dict) and a.get("uid") and a.get("password"):
            task = asyncio.create_task(account_loop_guest(str(a["uid"]), str(a["password"])))
            bot_state.account_workers[str(a["uid"])] = task

# ==================== API HANDLERS ====================
async def serve_index(request):
    for path in (INDEX_HTML_PATH, "index.html"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return aiohttp.web.Response(text=f.read(), content_type="text/html")
        except Exception: continue
    return aiohttp.web.Response(text="<h1>COMMAND CENTER</h1>", content_type="text/html")

async def api_stats(request):
    accs = list(bot_state.accounts.values())
    logs_out = [{"time": l.get("time",""), "message": l.get("message",""), "level": l.get("level","info")} for l in bot_state.logs[-100:]]
    return aiohttp.web.json_response({
        "accounts": accs,
        "logs": logs_out,
        "total_accounts": len(accs),
        "total_matches": bot_state.total_matches,
        "total_gained_exp": bot_state.total_exp_gained,
        "uptime": max(1, int(time.time() - bot_state.start_time))
    })

async def api_account_add(request):
    try:
        body = await request.json()
        uid = str(body.get("uid", "")).strip()
        password = str(body.get("password", "")).strip()
        if uid and password:
            asyncio.create_task(account_loop_guest(uid, password))
            return aiohttp.web.json_response({"status": "ok"})
        return aiohttp.web.json_response({"status": "error", "error": "UID and password required"})
    except Exception as e:
        return aiohttp.web.json_response({"status": "error", "error": str(e)})

async def api_create_bot_team(request):
    try:
        body = await request.json()
        team_name = body.get("team_name", "Team_RBC")
        players = body.get("players", [])
        print_success(f"[TEAM API] Creating custom team '{team_name}' with {len(players)} players")
        for p in players:
            uid = str(p.get("uid", "")).strip()
            password = str(p.get("pass", "")).strip()
            if uid and password:
                asyncio.create_task(account_loop_guest(uid, password))
                await asyncio.sleep(0.3)
        return aiohttp.web.json_response({"ok": True, "msg": f"Team {team_name} launched!"})
    except Exception as e:
        return aiohttp.web.json_response({"ok": False, "error": str(e)})

async def api_add_clan(request):
    try:
        body = await request.json()
        clan_uid = str(body.get("clan_uid", "")).strip()
        if not clan_uid:
            return aiohttp.web.json_response({"ok": False, "error": "clan_uid required"})
        count = 0
        for uid, ad in bot_state.account_credentials.items():
            token = ad.get('token')
            if token:
                asyncio.create_task(join_clan(token, clan_uid))
                count += 1
        return aiohttp.web.json_response({"ok": True, "msg": f"Clan request sent to {count} accounts"})
    except Exception as e:
        return aiohttp.web.json_response({"ok": False, "error": str(e)})

async def api_account_delete(request):
    try:
        body = await request.json()
        uid = str(body.get("uid", "")).strip()
        if uid in bot_state.account_workers:
            bot_state.account_workers[uid].cancel()
            bot_state.account_workers.pop(uid, None)
        if uid in bot_state.accounts:
            del bot_state.accounts[uid]
        return aiohttp.web.json_response({"status": "ok"})
    except Exception as e:
        return aiohttp.web.json_response({"status": "error", "error": str(e)})

async def api_account_refresh(request):
    try:
        body = await request.json()
        uid = str(body.get("uid", "")).strip()
        asyncio.create_task(refresh_account_profile(uid))
        return aiohttp.web.json_response({"status": "ok"})
    except Exception as e:
        return aiohttp.web.json_response({"status": "error", "error": str(e)})

async def api_logs_clear(request):
    bot_state.logs = []
    return aiohttp.web.json_response({"status": "ok"})

async def start_web_server():
    app = aiohttp.web.Application()
    app.router.add_get("/", serve_index)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_post("/api/account/add", api_account_add)
    app.router.add_post("/api/create_bot_team", api_create_bot_team)
    app.router.add_post("/api/add_clan", api_add_clan)
    app.router.add_post("/api/account/delete", api_account_delete)
    app.router.add_post("/api/account/refresh", api_account_refresh)
    app.router.add_post("/api/logs/clear", api_logs_clear)

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, WEB_HOST, WEB_PORT)
    await site.start()
    print_success(f"[WEB] Command Center → http://localhost:{WEB_PORT}")

async def main():
    print_info("Starting Free Fire Bot Server...")
    await start_web_server()
    await _autoload_accounts()
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
