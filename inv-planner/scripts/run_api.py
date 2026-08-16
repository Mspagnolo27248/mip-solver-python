"""Run the web app.

Usage:  python scripts/run_api.py [--port 8000] [--reload]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import uvicorn  # noqa: E402


def main() -> int:
    port = 8000
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    reload = "--reload" in sys.argv
    print("Inventory planner:  http://127.0.0.1:{}".format(port))
    uvicorn.run("invplanner.api.main:app", host="127.0.0.1", port=port,
                reload=reload, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
