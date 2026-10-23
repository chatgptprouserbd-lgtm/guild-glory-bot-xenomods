import os
import json
from aiohttp import web
from guild import GuildManager
from app import FreeFireEngine

app = web.Application()
guild_manager = GuildManager()
engine = FreeFireEngine()

ACCOUNTS_FILE = "accounts.json"
TOKEN_CACHE_FILE = "token_cache.json"

def save_account_data(uid, access_token, region="BD"):
    # 1. Accounts list update
    accounts = []
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    accounts = data
        except Exception:
            accounts = []
    
    existing = next((acc for acc in accounts if acc.get("uid") == uid), None)
    if not existing:
        accounts.append({"uid": uid, "region": region, "created_at": "2026-10-23 12:00:00"})
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(accounts, f, indent=2)

    # 2. Token cache dictionary update (Fixes 'root must be dict' error permanently)
    token_cache = {}
    if os.path.exists(TOKEN_CACHE_FILE):
        try:
            with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    token_cache = data
        except Exception:
            token_cache = {}

    token_cache[str(uid)] = {
        "uid": uid,
        "access_token": access_token,
        "region": region
    }

    with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(token_cache, f, indent=2)

async def index(request):
    return web.FileResponse('./templates/index.html')

async def handle_add_account(request):
    try:
        data = await request.post()
        uid = data.get("uid", "").strip()
        access_token = data.get("access_token", "").strip()
        region = data.get("region", "BD").strip().upper()

        if not uid or not access_token:
            return web.json_response({"status": "error", "message": "UID and Access Token are required!"}, status=400)

        save_account_data(uid, access_token, region)
        return web.json_response({"status": "success", "message": f"BD Account {uid} added successfully!"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def handle_join_guild(request):
    try:
        data = await request.json()
        guild_id = data.get("guild_id", "").strip()
        if not guild_id:
            return web.json_response({"status": "error", "message": "Guild ID required!"}, status=400)
        
        res = guild_manager.join_guild(guild_id)
        return web.json_response(res)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

# Routes setup
app.router.add_get('/', index)
app.router.add_post('/api/add_account', handle_add_account)
app.router.add_post('/api/join_guild', handle_join_guild)
app.router.add_static('/static', './templates')

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8080)
