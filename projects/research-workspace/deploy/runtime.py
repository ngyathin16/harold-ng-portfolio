"""Load only the credentials needed by the selected container process."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import quote


def secret(name):
    value = (Path(os.environ.get("RESEARCH_SECRET_DIR", "/run/secrets")) / name).read_text().strip()
    if len(value) < 24 or "\n" in value or "\r" in value:
        raise ValueError("Invalid secret file: " + name)
    return value


def database_url(user, password_file):
    return "postgresql://{}:{}@{}:{}/{}".format(quote(user, safe=""), quote(secret(password_file), safe=""),
        os.environ.get("PGHOST", "db"), os.environ.get("PGPORT", "5432"), quote(os.environ.get("PGDATABASE", "research"), safe=""))


def worker_process():
    child = subprocess.Popen([sys.executable, "-m", "service.worker"])
    def stop(signum, frame):
        child.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    heartbeat = Path("/tmp/worker-heartbeat.json")
    try:
        while child.poll() is None:
            temporary = heartbeat.with_suffix(".tmp")
            temporary.write_text(json.dumps({"pid": child.pid, "observed_at": time.time()}))
            temporary.replace(heartbeat)
            time.sleep(5)
        return child.returncode
    finally:
        heartbeat.unlink(missing_ok=True)


def main():
    mode, *args = sys.argv[1:]
    if mode == "adopt":
        import psycopg
        from .migrate import ROOT
        from .schema_baseline import adopt
        with psycopg.connect(database_url("postgres", "postgres_password")) as connection:
            print(json.dumps(adopt(connection, ROOT)))
        return
    if mode == "migrate":
        from .migrate import main as migrate
        migrate()
        return
    if mode in ("api", "worker", "cleanup"):
        role = "api" if mode == "api" else "worker"
        os.environ["RESEARCH_DATABASE_URL"] = database_url("research_" + role + "_login", role + "_password")
        if mode == "api":
            os.environ["RESEARCH_SESSION_SECRET"] = secret("session_secret")
            command = [sys.executable, "-m", "uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", os.environ["RESEARCH_PROXY_IP"]]
        elif mode == "worker":
            raise SystemExit(worker_process())
        else:
            command = [sys.executable, "-m", "service.worker", "--cleanup"]
    elif mode == "import":
        os.environ["DATABASE_URL"] = database_url("research_importer_login", "importer_password")
        command = [sys.executable, "data/import_snapshot.py", "publish", "/bundle", "--local-storage", "/objects"]
    elif mode in ("backup", "restore"):
        if mode == "backup":
            os.environ["RESEARCH_BACKUP_DATABASE_URL"] = database_url("research_backup_login", "backup_password")
        else:
            os.environ["RESEARCH_RESTORE_DATABASE_URL"] = database_url("postgres", "postgres_password")
        settings = json.loads(Path("/run/secrets/r2_config").read_text())
        allowed = {"R2_BUCKET", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "RESEARCH_BACKUP_PREFIX"}
        if not isinstance(settings, dict) or set(settings) - allowed or any(not isinstance(value, str) for value in settings.values()):
            raise ValueError("Invalid R2 secret configuration")
        os.environ.update(settings)
        command = [sys.executable, "-m", "ops.backup", *(args if mode == "backup" else ["restore", *args])]
    else:
        raise ValueError("Unknown container operation")
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Container operation failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
