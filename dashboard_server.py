# -*- coding: utf-8 -*-
"""
FreeFire Level Up Bot - Embedded Web Dashboard Server
"""
import asyncio, json, os, time
from typing import Dict, List, Any, Optional
from aiohttp import web

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")

class BotState:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.logs: List[Dict[str, Any]] = []
        self.total_matches = 0
        self.total_gained_exp = 0
        self.start_time = time.time()

bot_state = BotState()

async def handle_index(request: web.Request) -> web.Response:
    content = "<h1>COMMAND CENTER</h1>"
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    return web.Response(text=content, content_type="text/html", charset="utf-8")

async def handle_get_stats(request: web.Request) -> web.Response:
    accounts_data = list(bot_state.accounts.values())
    return web.json_response({
        "total_accounts": len(bot_state.accounts),
        "total_matches": bot_state.total_matches,
        "total_gained_exp": bot_state.total_gained_exp,
        "accounts": accounts_data,
        "logs": bot_state.logs[-100:],
        "uptime": int(time.time() - bot_state.start_time)
    })

async def start_web_dashboard(host: str = "0.0.0.0", port: int = 25567):
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/stats", handle_get_stats)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"[WEB] Dashboard running on http://{host}:{port}")
    return runner
