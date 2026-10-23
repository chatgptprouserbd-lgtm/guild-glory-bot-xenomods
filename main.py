import os
from dashboard_server import app
from aiohttp import web

if __name__ == "__main__":
    print("[*] DARK MASTER ENGINE (BD Server Optimized) is starting...")
    web.run_app(app, host="0.0.0.0", port=8080)
