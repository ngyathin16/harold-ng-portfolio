# Oracle free capacity monitor

This Windows helper checks host capacity for `VM.Standard.A1.Flex` at 2 OCPUs/12 GB and 1 OCPU/6 GB. It verifies the configured region is the tenancy's home region and queries each availability domain. It never launches an instance, reserves capacity, changes subscriptions, or falls back to a paid shape. Capacity does not prove Always Free billing eligibility; verify eligibility separately before provisioning.

The implementation uses Oracle's [compute capacity report API](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/core/client/oci.core.ComputeClient.html#oci.core.ComputeClient.create_compute_capacity_report). An available result is a momentary observation, not a reservation. The checker continues until its fixed deadline even after detecting availability.

## Install on Windows

Prerequisites, kept outside the repository:

- An OCI config and matching API signing key in `$env:USERPROFILE/.oci/research-workspace/`.
- A Python virtual environment at `tools-env/` in that private directory, with the `oci` SDK installed. Verified with OCI 2.192.0.
- Restrictive permissions on that private directory: the current user and SYSTEM only. Generated files inherit those permissions.

From this directory, run:

```powershell
./install-capacity-checker.ps1
```

The installer copies the checker to the private directory and registers **Research Workspace - Oracle free capacity** for the current user without storing a Windows password. It runs immediately, then on a schedule every 90 minutes for three days. One final local run marks the report expired. Reinstalling refuses to replace an existing task or silently extend its deadline.

The PC must be on and the user signed in; locking the desktop is fine. Missed checks can run after resume, but the checker refuses Oracle calls once its deadline has passed. It does not wake the PC. Runs are limited to five minutes, cannot overlap, and do not loop or retry failed Oracle requests. The expired task is retained for inspection and makes no further Oracle requests.

## Read results and stop

Reports live under `$env:USERPROFILE/.oci/research-workspace/capacity-monitor/`:

- `STATUS.txt`: readable status, last run and deadline, in UTC.
- `latest.json` and `history.jsonl`: current result and run history. `ERROR` is a failed check; `UNKNOWN` is an inconclusive capacity response. Neither means no capacity.
- `last-available.json`: most recent available result, retained even when later checks fail or find no capacity.
- `schedule.json`: the installed schedule.

The helper attempts a Windows notification when a check changes to `AVAILABLE`. Notifications depend on desktop settings; the files remain authoritative. No email, chat or other external notification is sent. Reports omit credentials, tenancy/user IDs, availability-domain names and exception messages.

```powershell
$task = 'Research Workspace - Oracle free capacity'
Get-ScheduledTaskInfo -TaskName $task
Get-Content "$env:USERPROFILE/.oci/research-workspace/capacity-monitor/STATUS.txt"

# Stop early. The reports and private credentials remain on disk.
Disable-ScheduledTask -TaskName $task
Stop-ScheduledTask -TaskName $task

# Remove the schedule. Run the installer again only to start a new window.
Unregister-ScheduledTask -TaskName $task -Confirm:$false
```

Tests use fake capacity responses and do not require credentials, the OCI SDK or a cloud account:

```powershell
python -m unittest discover -s ops -p test_oracle_capacity.py -v
```

Run that command from `projects/research-workspace/`. Scheduler installation and notification delivery require Windows; Linux CI checks the polling, expiry and report behavior.
