"""Container-local database and worker process checks; no public diagnostics."""
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

import psycopg

from .runtime import database_url


def check_worker(path=Path("/tmp/worker-heartbeat.json")):
    observed = json.loads(path.read_text())
    age = time.time() - observed["observed_at"]
    if age < 0 or age > 20:
        raise ValueError("Worker process heartbeat is stale")
    os.kill(observed["pid"], 0)
    with psycopg.connect(database_url("research_worker_login", "worker_password"), connect_timeout=5) as connection:
        connection.execute("SELECT 1 FROM research.query_runs LIMIT 1")


if __name__ == "__main__":
    try:
        if sys.argv[1] == "worker":
            check_worker()
        else:
            with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=5) as response:
                if response.status != 200:
                    raise ValueError("API is unavailable")
    except Exception:
        raise SystemExit(1)
