"""Linux host commands for deployment, status, import, and scheduled maintenance."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PASSWORDS = ("postgres_password", "api_password", "worker_password", "importer_password", "backup_password", "session_secret")
DEFAULT_CONFIG = Path("/etc/research-workspace/config.env")


def read_config(path, local=False):
    values = {}
    for raw in path.read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        key, separator, value = raw.partition("=")
        if not separator or key in values or re.search(r"[\r\n\x00$`'\"]", value):
            raise ValueError("Malformed deployment setting")
        values[key] = value
    required = {"APP_COMMIT", "API_HOSTNAME", "STATE_ROOT", "SECRET_ROOT", "RESEARCH_ALLOWED_ORIGINS"}
    if not required <= values.keys() or any("REPLACE_" in value for value in values.values()):
        raise ValueError("Deployment settings are incomplete")
    if not re.fullmatch(r"[a-f0-9]{40}", values["APP_COMMIT"]):
        raise ValueError("APP_COMMIT must be an exact Git commit")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", values.get("PGDATABASE", "research")):
        raise ValueError("PGDATABASE must be a simple database name")
    hostname = values["API_HOSTNAME"]
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", hostname) and not (local and hostname == "http://localhost"):
        raise ValueError("API_HOSTNAME must be a hostname; local rehearsal requires --local")
    for key in ("STATE_ROOT", "SECRET_ROOT"):
        value = Path(values[key])
        if not value.is_absolute() or len(value.parts) < 3 or value.is_symlink():
            raise ValueError(key + " must be an explicit dedicated absolute directory")
    for key in ("RESEARCH_POOL_SIZE", "RESEARCH_MAX_QUEUED", "RESEARCH_WORKER_CONCURRENCY", "RESEARCH_MAX_SECONDS", "RESEARCH_MAX_RESULTS", "RESEARCH_RESULT_RETENTION_DAYS", "RESEARCH_MIN_FREE_BYTES"):
        if int(values.get(key, "1")) < 1:
            raise ValueError(key + " must be positive")
    return values


def compose(config, *args, local=False, capture=False):
    command = ["docker", "compose", "--env-file", str(config), "-f", str(HERE / "compose.yaml")]
    if local:
        command += ["-f", str(HERE / "compose.local.yaml")]
    command += list(args)
    return subprocess.run(command, check=True, text=True, capture_output=capture)


def init(config, state, hostname, origin):
    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("Initialize on Linux as root so secret and volume permissions are enforced")
    if config.exists():
        raise ValueError("Config already exists; initialization never overwrites an installation")
    secret_root = config.parent / "secrets"
    for directory in (config.parent, secret_root, state):
        if not directory.is_absolute() or len(directory.parts) < 3 or directory.is_symlink():
            raise ValueError("Use dedicated absolute directories")
    config.parent.mkdir(parents=True, exist_ok=True)
    secret_root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(secret_root, 0o700)
    for name in PASSWORDS:
        path = secret_root / name
        with path.open("x") as stream:
            stream.write(secrets.token_urlsafe(36))
        os.chown(path, 0, 10001)
        os.chmod(path, 0o440)
    r2 = secret_root / "r2_config"
    r2.write_text("{}\n")
    os.chown(r2, 0, 10001)
    os.chmod(r2, 0o440)
    for name in ("postgres", "objects", "bundle", "backup-work", "restore-work", "capacity", "caddy-data", "caddy-config", "reports"):
        directory = state / name
        directory.mkdir(parents=True, exist_ok=True)
        if name in ("objects", "backup-work", "restore-work", "bundle", "reports"):
            os.chown(directory, 10001, 10001)
            os.chmod(directory, 0o700)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, check=True, capture_output=True, text=True).stdout.strip()
    text = (HERE / "config.env.example").read_text().replace("REPLACE_WITH_REVIEWED_GIT_COMMIT", commit).replace("REPLACE_WITH_API_HOSTNAME", hostname)
    text = text.replace("STATE_ROOT=/srv/research-workspace", "STATE_ROOT=" + str(state)).replace("SECRET_ROOT=/etc/research-workspace/secrets", "SECRET_ROOT=" + str(secret_root)).replace("RESEARCH_ALLOWED_ORIGINS=https://haroldng.dev", "RESEARCH_ALLOWED_ORIGINS=" + origin)
    config.write_text(text)
    os.chmod(config, 0o600)
    print("Created deployment configuration and separate service credentials at " + str(config))


def validate_secrets(settings, backup=False):
    root = Path(settings["SECRET_ROOT"])
    for name in (*PASSWORDS, *(('r2_config',) if backup else ())):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing or invalid service secret: " + name)
        if os.name == "posix" and (path.stat().st_mode & 0o027 or path.stat().st_uid not in (0, os.geteuid())):
            raise ValueError("Secret ownership or permissions are unsafe: " + name)
        if name != "r2_config" and len(path.read_text().strip()) < 24:
            raise ValueError("Missing or invalid service secret: " + name)
    if backup:
        value = json.loads((root / "r2_config").read_text())
        if any(not value.get(key) for key in ("R2_BUCKET", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")):
            raise ValueError("R2 configuration is incomplete")


def verify_source(settings, local=False):
    root = HERE.parent
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    if commit != settings["APP_COMMIT"]:
        raise ValueError("APP_COMMIT differs from the checked-out source; checkout the intended release first")
    changed = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", "."], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    if changed and not local:
        raise ValueError("Production build requires a clean backend build context")
    os.environ["APP_SOURCE_STATE"] = "uncommitted-local-rehearsal" if changed else "clean"
    return {"commit": commit, "source_state": os.environ["APP_SOURCE_STATE"]}


@contextmanager
def operation_lock(state):
    import fcntl
    with (state / "operation.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--local", action="store_true", help="Use loopback HTTP only for a disposable local rehearsal")
    sub = parser.add_subparsers(dest="action", required=True)
    start = sub.add_parser("init")
    start.add_argument("--state", type=Path, default=Path("/srv/research-workspace"))
    start.add_argument("--hostname", required=True)
    start.add_argument("--origin", default="https://haroldng.dev")
    for action in ("check", "up", "status", "import", "cleanup", "backup", "restart", "adopt"):
        sub.add_parser(action)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup-id", required=True)
    schedule = sub.add_parser("scheduled-backup")
    schedule.add_argument("--notify", type=Path, required=True, help="Executable failure notifier; receives the sanitized status path")
    args = parser.parse_args()
    if args.action == "init":
        init(args.config, args.state, args.hostname, args.origin)
        return
    settings = read_config(args.config, args.local)
    validate_secrets(settings, backup=args.action in ("backup", "scheduled-backup", "restore"))
    if args.action == "check":
        compose(args.config, "config", "--quiet", local=args.local)
        print("Configuration validated; no services changed")
        return
    if args.action == "status":
        compose(args.config, "ps", "--all", local=args.local)
        return
    state = Path(settings["STATE_ROOT"])
    with operation_lock(state):
        if args.action == "up":
            source = verify_source(settings, args.local)
            compose(args.config, "build", "api", "worker", "migrate", "importer", "cleanup", "backup", local=args.local)
            compose(args.config, "up", "-d", "--wait", "db", local=args.local)
            compose(args.config, "stop", "api", "worker", local=args.local)
            compose(args.config, "run", "--rm", "migrate", local=args.local)
            compose(args.config, "up", "-d", "--wait", "api", "worker", "proxy", local=args.local)
            result = compose(args.config, "images", "--format", "json", local=args.local, capture=True)
            (state / "reports" / "release-images.json").write_text(result.stdout)
            (state / "reports" / "release-source.json").write_text(json.dumps(source))
        elif args.action == "restore":
            verify_source(settings, args.local)
            if not settings.get("PGDATABASE", "research").endswith("_restore_test"):
                raise ValueError("Recovery must use a separately configured *_restore_test database")
            objects = state / "objects"
            if objects.is_symlink() or any(objects.iterdir()):
                raise ValueError("Recovery requires an empty object directory")
            work = state / "restore-work"
            before = set(work.iterdir())
            compose(args.config, "build", "api", "worker", "migrate", "adopt", "restore", local=args.local)
            compose(args.config, "up", "-d", "--wait", "db", local=args.local)
            compose(args.config, "run", "--rm", "restore", "restore", "--backup-id", args.backup_id, "--work-dir", "/restore-work", local=args.local)
            recovered = [path for path in set(work.iterdir()) - before if path.is_dir() and (path / "restore-report.json").is_file()]
            if len(recovered) != 1 or recovered[0].is_symlink():
                raise ValueError("Expected one verified recovery directory")
            report = json.loads((recovered[0] / "restore-report.json").read_text())
            if not report.get("passed") or report.get("backup_id") != args.backup_id:
                raise ValueError("Recovery report is not successful for the requested set")
            manifest = json.loads((recovered[0] / "manifest.json").read_text())
            if manifest.get("applied_migrations") is None:
                compose(args.config, "run", "--rm", "adopt", local=args.local)
            compose(args.config, "run", "--rm", "migrate", local=args.local)
            os.replace(recovered[0] / "objects", objects)
            compose(args.config, "up", "-d", "--wait", "api", "worker", "proxy", local=args.local)
            print("Restored services started; complete the isolated record, statistics, and worker checks before promotion")
        elif args.action == "restart":
            compose(args.config, "restart", "db", "api", "worker", "proxy", local=args.local)
            compose(args.config, "up", "-d", "--wait", "db", "api", "worker", "proxy", local=args.local)
        elif args.action == "scheduled-backup":
            if not args.notify.is_absolute() or not os.access(args.notify, os.X_OK):
                raise ValueError("An executable operator failure notifier is required")
            report = state / "reports" / "scheduled-backup.json"
            try:
                compose(args.config, "run", "--rm", "backup", local=args.local)
            except Exception as error:
                report.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "passed": False, "error_type": type(error).__name__}))
                subprocess.run([str(args.notify), str(report)], check=True)
                raise
            else:
                report.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "passed": True}))
        else:
            service = "importer" if args.action == "import" else args.action
            compose(args.config, "run", "--rm", service, local=args.local)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Deployment operation failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
