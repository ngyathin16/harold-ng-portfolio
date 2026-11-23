"""Read-only A1 capacity monitor. Credentials and runtime reports stay outside Git."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


SHAPE = "VM.Standard.A1.Flex"
SIZES = ((2, 12), (1, 6))


def utc_now():
    return datetime.now(timezone.utc)


def parse_deadline(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Deadline must include a timezone")
    return parsed.astimezone(timezone.utc)


def require_before(deadline):
    if utc_now() >= deadline:
        raise TimeoutError("Monitoring window ended")


def query_capacity(config_path, deadline):
    import oci

    require_before(deadline)
    config = oci.config.from_file(str(config_path))
    options = {"timeout": (10, 25), "retry_strategy": oci.retry.NoneRetryStrategy()}
    identity = oci.identity.IdentityClient(config, **options)
    subscriptions = identity.list_region_subscriptions(config["tenancy"]).data
    home = next(region.region_name for region in subscriptions if region.is_home_region)
    if config["region"] != home:
        raise ValueError("Only the home region may be checked")
    require_before(deadline)
    domains = identity.list_availability_domains(config["tenancy"]).data
    compute = oci.core.ComputeClient(config, **options)
    rows = []
    for index, domain in enumerate(domains, start=1):
        require_before(deadline)
        details = oci.core.models.CreateComputeCapacityReportDetails(
            compartment_id=config["tenancy"],
            availability_domain=domain.name,
            shape_availabilities=[
                oci.core.models.CreateCapacityReportShapeAvailabilityDetails(
                    instance_shape=SHAPE,
                    instance_shape_config=oci.core.models.CapacityReportInstanceShapeConfig(
                        ocpus=cpu, memory_in_gbs=memory
                    ),
                )
                for cpu, memory in SIZES
            ],
        )
        report = compute.create_compute_capacity_report(details).data
        for item in report.shape_availabilities:
            rows.append({
                "availability_domain": index,
                "shape": SHAPE,
                "ocpus": item.instance_shape_config.ocpus,
                "memory_gb": item.instance_shape_config.memory_in_gbs,
                "status": item.availability_status,
            })
    return {"region": home, "results": rows}


def classify(rows):
    if any(row["status"] == "AVAILABLE" for row in rows):
        return "AVAILABLE"
    if rows and all(row["status"] == "OUT_OF_HOST_CAPACITY" for row in rows):
        return "UNAVAILABLE"
    return "UNKNOWN"


def safe_error(error):
    result = {"type": type(error).__name__}
    status = getattr(error, "status", None)
    code = getattr(error, "code", None)
    if isinstance(status, int):
        result["http_status"] = status
    if isinstance(code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", code):
        result["code"] = code
    return result


def atomic_write(path, text):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".capacity-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def single_run(directory):
    with (directory / "checker.lock").open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        try:
            yield True
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def notify_available():
    if os.name != "nt":
        return
    command = """Add-Type -AssemblyName System.Windows.Forms
$notice = New-Object System.Windows.Forms.NotifyIcon
try {
    $notice.Icon = [System.Drawing.SystemIcons]::Information
    $notice.Visible = $true
    $notice.ShowBalloonTip(8000, 'Oracle A1 capacity detected', 'Check the Research Workspace capacity report. No server was created; capacity can change.', [System.Windows.Forms.ToolTipIcon]::Info)
    $until = [DateTime]::UtcNow.AddSeconds(8)
    while ([DateTime]::UtcNow -lt $until) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 100
    }
} finally { $notice.Dispose() }
"""
    executable = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run(
        [str(executable), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", command],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=True,
    )


def write_status(directory, report):
    atomic_write(directory / "latest.json", json.dumps(report, indent=2) + "\n")
    lines = [
        "Oracle Always Free capacity checker", "",
        "Status: " + report["status"],
        "Last run (UTC): " + report["checked_at"],
        "Stops checking (UTC): " + report["expires_at"],
        "Region: " + report.get("region", "not checked"),
        "",
    ]
    for row in report.get("results", []):
        lines.append(f"AD {row['availability_domain']}: {row['ocpus']:g} OCPU / {row['memory_gb']:g} GB: {row['status']}")
    if "error" in report:
        lines.append("Check failed: " + json.dumps(report["error"]))
    if "last_available_at" in report:
        lines.append("Last capacity seen (UTC): " + report["last_available_at"])
    lines += ["", "Read-only: no server, reservation or paid resource is created.",
              "Capacity is a snapshot, not a reservation or a billing eligibility check.",
              "Runs every 3 hours while this PC is on and your account is signed in.",
              "Windows notifications are best effort; latest.json and history.jsonl are the record."]
    atomic_write(directory / "STATUS.txt", "\n".join(lines) + "\n")
    with (directory / "history.jsonl").open("a", encoding="utf-8") as history:
        history.write(json.dumps(report) + "\n")


def check_once(config_path, directory, deadline, query=query_capacity, notify=notify_available):
    directory.mkdir(parents=True, exist_ok=True)
    with single_run(directory) as acquired:
        if not acquired:
            return 0
        previous = {}
        try:
            previous = json.loads((directory / "latest.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        now = utc_now()
        report = {"checked_at": now.isoformat(), "expires_at": deadline.isoformat(),
                  "resources_created": False}
        if previous.get("last_available_at"):
            report["last_available_at"] = previous["last_available_at"]
        if now >= deadline:
            report["status"] = "EXPIRED"
        else:
            try:
                report.update(query(config_path, deadline))
                report["status"] = "EXPIRED" if utc_now() >= deadline else classify(report["results"])
            except Exception as error:
                report["status"] = "EXPIRED" if utc_now() >= deadline else "ERROR"
                report["error"] = safe_error(error)
        if report["status"] == "AVAILABLE":
            report["last_available_at"] = report["checked_at"]
            atomic_write(directory / "last-available.json", json.dumps(report, indent=2) + "\n")
        write_status(directory, report)
        if (report["status"] == "AVAILABLE" and previous.get("status") != "AVAILABLE"
                and utc_now() < deadline):
            try:
                notify()
            except Exception:
                # Notifications must never prevent the durable report from being written.
                pass
        return 1 if report["status"] in {"ERROR", "UNKNOWN"} else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--expires-at", type=parse_deadline, required=True)
    args = parser.parse_args()
    return check_once(args.config, args.state_dir, args.expires_at)


if __name__ == "__main__":
    raise SystemExit(main())
