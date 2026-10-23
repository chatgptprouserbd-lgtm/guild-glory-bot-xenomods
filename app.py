import time

class FreeFireEngine:
    def __init__(self):
        self.uptime_start = time.time()

    def get_engine_status(self):
        uptime = int(time.time() - self.uptime_start)
        return {
            "status": "Running",
            "server": "BD",
            "uptime_seconds": uptime
        }
