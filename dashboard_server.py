# -*- coding: utf-8 -*-
"""
FreeFire Level Up Bot - Web Dashboard & Clan / Team Management API
Embedded Async Web Server (aiohttp)
"""

import asyncio
import json
import os
import time
from typing import Dict, List, Any, Optional
from aiohttp import web
from guild import join_guild

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")

EXP_TABLE: Dict[int, int] = {
    1: 0, 2: 48, 3: 202, 4: 544, 5: 1012, 6: 1844, 7: 2792, 8: 3800,
    9: 4870, 10: 6004, 11: 7192, 12: 8448, 13: 9760, 14: 11140, 15: 12566,
    16: 14060, 17: 15610, 18: 17224, 19: 18902, 20: 20632, 21: 22424, 22: 24278,
    23: 26192, 24: 28166, 25: 30200, 26: 32294, 27: 34448, 28: 37804, 29: 41274,
    30: 44870, 31: 48582, 32: 53394, 33: 58566, 34: 64096, 35: 69994, 36: 76460,
    37: 83506, 38: 91128, 39: 99322, 40: 108092, 41: 120144, 42: 133266, 43: 147472,
    44: 162760, 45: 179126, 46: 196572, 47: 215368, 48: 235516, 49: 257010, 50: 279860,
    51: 304056, 52: 348318, 53: 394982, 54: 444044, 55: 495508, 56: 549364, 57: 633756,
    58: 721744, 59: 813336, 60: 908522, 61: 1041438, 62: 1180352, 63: 1325266,
    64: 1476184, 65: 1634300, 66: 1840946, 67: 2056594, 68: 2281242, 69: 2514880,
    70: 2757530, 71: 3059506, 72: 3372284, 73: 3699456, 74: 4041030, 75: 4397002,
    76: 4829104, 77: 5282204, 78: 5756304, 79: 6251408, 80: 6776502, 81: 7381324,
    82: 8043154, 83: 8752982, 84: 9510808, 85: 10316338, 86: 11277190, 87: 12291748,
    88: 13360304, 89: 14482858, 90: 15659418, 91: 17026708, 92: 18453950, 93: 19941280,
    94: 21488570, 95: 23095858, 96: 24763138, 97: 26490428, 98: 28378704, 99: 30124996,
    100: 32032884
}


def calculate_level_progress(level: int, current_exp: int) -> Dict[str, Any]:
    level = max(1, level)
    next_level = min(100, level + 1)
    base_exp = EXP_TABLE.get(level, 0)
    target_exp = EXP_TABLE.get(next_level, base_exp + 50000)

    needed_for_level = max(1, target_exp - base_exp)
    earned_in_level = max(0, current_exp - base_exp)
    remaining_exp = max(0, target_exp - current_exp)
    progress_pct = min(100.0, max(0.0, (earned_in_level / needed_for_level) * 100.0))

    return {
        "next_level": next_level,
        "base_exp": base_exp,
        "target_exp": target_exp,
        "needed_for_level": needed_for_level,
        "earned_in_level": earned_in_level,
        "remaining_exp": remaining_exp,
        "progress_pct": round(progress_pct, 1)
    }


class BotState:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.teams: Dict[str, Dict[str, Any]] = {}
        self.logs: List[Dict[str, Any]] = []
        self.max_logs = 200
        self.total_matches = 0
        self.total_matches_started = 0
        self.total_gained_exp = 0
        self.start_time = time.time()
        self.account_workers: Dict[str, asyncio.Task] = {}
        self.account_token_map: Dict[str, str] = {}
        self.auth_to_game_id: Dict[str, str] = {}
        self.game_to_auth_id: Dict[str, str] = {}
        self.paused_accounts: set = set()
        self.refresh_callbacks: Dict[str, Any] = {}
        self.account_credentials: Dict[str, Dict[str, Any]] = {}
        self.active_writers: Dict[str, set] = {}

    def log(self, message: str, level: str = "info", uid: Optional[str] = None):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "uid": str(uid) if uid else None
        }
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)

    def register_account(self, uid: str, nickname: str, region: str, level: int, exp: int,
                         likes: int = 0, token: Optional[str] = None, auth_uid: Optional[str] = None):
        uid_str = str(uid)
        auth_uid_str = str(auth_uid) if auth_uid else self.game_to_auth_id.get(uid_str, "")
        if auth_uid_str:
            self.auth_to_game_id[auth_uid_str] = uid_str
            self.game_to_auth_id[uid_str] = auth_uid_str

        prog = calculate_level_progress(level or 1, exp)
        lvl_val = level or 1

        if uid_str not in self.accounts:
            self.accounts[uid_str] = {
                "uid": uid_str,
                "auth_uid": auth_uid_str or "",
                "nickname": nickname or f"Player_{uid_str[:6]}",
                "region": region or "BD",
                "level": lvl_val,
                "next_level": prog["next_level"],
                "mode": "BR",
                "initial_exp": exp,
                "current_exp": exp,
                "gained_exp": 0,
                "progress_pct": prog["progress_pct"],
                "status": "ONLINE",
                "matches_played": 0,
                "in_guild": False,
                "guild_id": "",
                "last_updated": time.strftime("%H:%M:%S")
            }
        else:
            acc = self.accounts[uid_str]
            acc["current_exp"] = exp
            acc["level"] = lvl_val
            acc["gained_exp"] = max(0, exp - acc["initial_exp"])
            acc["progress_pct"] = prog["progress_pct"]
            acc["last_updated"] = time.strftime("%H:%M:%S")
        self.recalc_totals()

    def add_bulk_team(self, team_id: str, team_name: str, players: List[Dict[str, str]], clan_id: str = ""):
        self.teams[team_id] = {
            "id": team_id,
            "name": team_name,
            "players": players,
            "player_count": len(players),
            "clan_id": clan_id,
            "status": "ACTIVE",
            "in_guild": bool(clan_id),
            "created_at": time.strftime("%H:%M:%S")
        }
        self.log(f"👥 Bulk Team Created: '{team_name}' with {len(players)} players", "success")

    def recalc_totals(self):
        self.total_gained_exp = sum(acc.get("gained_exp", 0) for acc in self.accounts.values())


bot_state = BotState()


async def handle_index(request: web.Request) -> web.Response:
    content = "<h1>COMMAND CENTER LOADING...</h1>"
    if os.path.exists(TEMPLATE_PATH):
        try:
            with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            pass
    return web.Response(text=content, content_type="text/html", charset="utf-8")


async def handle_get_stats(request: web.Request) -> web.Response:
    accounts_data = list(bot_state.accounts.values())
    teams_data = list(bot_state.teams.values())
    uptime_sec = max(1, int(time.time() - bot_state.start_time))

    return web.json_response({
        "total_accounts": len(bot_state.accounts),
        "total_teams": len(bot_state.teams),
        "total_matches": bot_state.total_matches,
        "total_gained_exp": bot_state.total_gained_exp,
        "accounts": accounts_data,
        "teams": teams_data,
        "logs": bot_state.logs[-80:],
        "uptime": uptime_sec
    })


async def handle_create_bulk_teams(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        teams = data.get("teams", [])
        if not teams:
            return web.json_response({"status": "error", "error": "No teams provided"}, status=400)

        created_count = 0
        accounts_file = "accounts.json"
        existing_accs = []
        if os.path.exists(accounts_file):
            try:
                with open(accounts_file, "r", encoding="utf-8") as f:
                    existing_accs = json.load(f)
            except Exception:
                existing_accs = []

        for idx, team in enumerate(teams, start=1):
            team_name = team.get("team_name", f"Squad_Team_{idx}")
            players = team.get("players", [])
            team_id = f"team_{int(time.time())}_{idx}"

            valid_players = []
            for p in players:
                uid = str(p.get("uid", "")).strip()
                password = str(p.get("password", "")).strip()
                if uid and password:
                    valid_players.append({"uid": uid, "password": password})
                    existing_accs = [a for a in existing_accs if str(a.get("uid", "")) != uid]
                    existing_accs.append({"uid": uid, "password": password})

            if valid_players:
                bot_state.add_bulk_team(team_id, team_name, valid_players)
                created_count += 1

                if "on_team_added" in bot_state.refresh_callbacks:
                    asyncio.create_task(bot_state.refresh_callbacks["on_team_added"](valid_players))

        with open(accounts_file, "w", encoding="utf-8") as f:
            json.dump(existing_accs, f, indent=2)

        return web.json_response({"status": "ok", "created_teams": created_count})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def handle_assign_clan(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        clan_id = str(data.get("clan_id", "")).strip()
        team_id = str(data.get("team_id", "")).strip()

        if not clan_id:
            return web.json_response({"status": "error", "error": "Clan ID is required"}, status=400)

        players_to_join = []
        if team_id and team_id in bot_state.teams:
            team = bot_state.teams[team_id]
            if team.get("in_guild"):
                return web.json_response({
                    "status": "warning",
                    "message": "This squad is already in a guild!"
                })
            players_to_join = team.get("players", [])
        else:
            # Join all active accounts
            for acc in bot_state.accounts.values():
                if acc.get("uid"):
                    players_to_join.append({"uid": acc["uid"], "password": acc.get("password", "")})

        success_cnt = 0
        already_cnt = 0
        fail_cnt = 0

        for p in players_to_join:
            uid = p.get("uid")
            pwd = p.get("password", "")
            if not uid: continue

            # Run guild join request
            ok, msg, already = await asyncio.to_thread(join_guild, clan_id, uid, pwd)
            if ok:
                success_cnt += 1
                if uid in bot_state.accounts:
                    bot_state.accounts[uid]["in_guild"] = True
                    bot_state.accounts[uid]["guild_id"] = clan_id
            elif already:
                already_cnt += 1
                if uid in bot_state.accounts:
                    bot_state.accounts[uid]["in_guild"] = True
            else:
                fail_cnt += 1

        if team_id and team_id in bot_state.teams:
            if success_cnt > 0 or already_cnt > 0:
                bot_state.teams[team_id]["in_guild"] = True
                bot_state.teams[team_id]["clan_id"] = clan_id
                bot_state.teams[team_id]["status"] = "IN_GUILD"

        msg = f"Clan Assignment Done: {success_cnt} Joined | {already_cnt} Already in Guild | {fail_cnt} Failed"
        bot_state.log(msg, "info")
        return web.json_response({
            "status": "ok",
            "message": msg,
            "success_count": success_cnt,
            "already_count": already_cnt,
            "fail_count": fail_cnt
        })
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def handle_clear_logs(request: web.Request) -> web.Response:
    bot_state.logs.clear()
    return web.json_response({"status": "ok"})


async def start_web_dashboard(host: str = "0.0.0.0", port: int = 20333):
    app = web.Application()

    @web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            return web.Response(status=204, headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type"
            })
        resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    app.middlewares.append(cors_middleware)

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/stats", handle_get_stats)
    app.router.add_post("/api/teams/bulk_create", handle_create_bulk_teams)
    app.router.add_post("/api/clan/assign", handle_assign_clan)
    app.router.add_post("/api/logs/clear", handle_clear_logs)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"[WEB] Server running on http://{host}:{port}")
    return runner
