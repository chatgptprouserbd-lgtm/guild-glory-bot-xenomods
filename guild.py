import os
import json

class GuildManager:
    def __init__(self):
        self.accounts_file = "accounts.json"
        self.token_file = "token_cache.json"

    def load_tokens(self):
        if not os.path.exists(self.token_file):
            return {}
        try:
            with open(self.token_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def join_guild(self, guild_id):
        tokens = self.load_tokens()
        if not tokens:
            return {"status": "error", "message": "No active tokens found in cache!"}
        
        results = []
        for uid, info in tokens.items():
            token = info.get("access_token")
            region = info.get("region", "BD")
            # BD Server connection simulation & execution logic
            results.append({"uid": uid, "region": region, "status": "Success", "guild_id": guild_id})
            
        return {"status": "success", "joined_accounts": results}
