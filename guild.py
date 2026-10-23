# -*- coding: utf-8 -*-
"""
Guild Join & Clan Automation Module for FreeFire Bot
"""

import json
import time
import requests

API_BASE = "https://danger-guild-management-web.vercel.app/join"
ACCOUNTS_FILE = "accounts.json"


def load_accounts(filepath=ACCOUNTS_FILE):
    """Load accounts from a JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict) and "accounts" in data:
            data = data["accounts"]

        if not isinstance(data, list):
            raise ValueError("accounts.json must contain a JSON array of objects")

        return data
    except FileNotFoundError:
        print(f"[ERROR] {filepath} not found.")
        return []
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON in {filepath}: {e}")
        return []


def join_guild(guild_id, uid, password, session=None):
    """
    Send GET request to join guild endpoint.
    Returns: (success: bool, message: str, already_joined: bool)
    """
    url = f"{API_BASE}?guild_id={guild_id}&uid={uid}&password={password}"

    try:
        requester = session if session else requests
        resp = requester.get(url, timeout=15)

        try:
            body = resp.json()
        except ValueError:
            body = resp.text

        resp_str = str(body).lower()

        # Check if already in guild
        if "already" in resp_str and ("guild" in resp_str or "clan" in resp_str or "member" in resp_str):
            return False, "This account/squad is already in a guild!", True

        if resp.status_code == 200:
            return True, f"SUCCESS — {body}", False
        else:
            return False, f"HTTP {resp.status_code} — {body}", False

    except requests.exceptions.Timeout:
        return False, "Request timed out", False
    except requests.exceptions.ConnectionError:
        return False, "Connection error", False
    except Exception as e:
        return False, f"Unexpected error: {e}", False


def main():
    print("=" * 55)
    print("   Guild Join Bot — Danger Guild Management")
    print("=" * 55)

    guild_id = input("Enter Guild ID : ").strip()
    if not guild_id:
        print("[ERROR] Guild ID cannot be empty.")
        return

    accounts = load_accounts(ACCOUNTS_FILE)
    if not accounts:
        print("[ERROR] No accounts loaded.")
        return

    print("-" * 55)
    print(f"[INFO] Guild ID   : {guild_id}")
    print(f"[INFO] Accounts   : {len(accounts)} found")
    print("-" * 55)

    session = requests.Session()
    success_count = 0
    fail_count = 0
    already_count = 0

    for idx, acc in enumerate(accounts, start=1):
        uid = str(acc.get("uid", "")).strip()
        password = str(acc.get("password", "")).strip()

        if not uid or not password:
            print(f"[{idx}/{len(accounts)}] SKIPPED — missing credentials")
            fail_count += 1
            continue

        masked_pw = password[:2] + "*" * (len(password) - 2) if len(password) > 2 else "***"
        print(f"[{idx}/{len(accounts)}] UID: {uid} | PW: {masked_pw} ... ", end="", flush=True)

        ok, msg, already = join_guild(guild_id, uid, password, session)

        if ok:
            success_count += 1
            print("✅ JOINED")
        elif already:
            already_count += 1
            print(f"⚠️ {msg}")
        else:
            fail_count += 1
            print(f"❌ FAILED — {msg}")

        time.sleep(0.5)

    print("-" * 55)
    print(f"[DONE] Success: {success_count} | Already in Guild: {already_count} | Failed: {fail_count}")
    print("=" * 55)


if __name__ == "__main__":
    main()
