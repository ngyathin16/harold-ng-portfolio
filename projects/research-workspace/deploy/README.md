# Deploy the research services

This package targets an Ubuntu 24.04 LTS Linux VM with Docker Engine, Docker Compose 2.24.4 or newer, Python 3, Git, and persistent disk. Debian 12 container images support `linux/amd64` and `linux/arm64`. Native ARM search timing remains a hosted check. No command allocates a cloud server or changes the Oracle capacity monitor.

Keep the portfolio on its existing Cloudflare host. This stack runs PostgreSQL 17, the API, a separate worker, and Caddy HTTPS ingress. R2 holds private recovery copies. The database and API have no published ports. Only the proxy publishes 80 and 443.

## Prepare the host

1. Install Docker Engine and its Compose plugin using [Docker's Ubuntu installation instructions](https://docs.docker.com/engine/install/ubuntu/). Record the installed versions with `docker version` and `docker compose version` in the deployment report. The host Docker version is a prerequisite, not an application dependency downloaded by a bootstrap shell script.
2. Configure the provider firewall to allow TCP 80 and 443, and TCP 22 only from the operator's CIDR. Keep an existing SSH session open while checking a second connection. Do not open port 5432 or 8080. Docker port publishing can bypass host firewall rules; enforce this boundary in the provider firewall as well.
3. Mount persistent storage at `/srv/research-workspace`. The `postgres`, `objects`, and `capacity` directories must be on that same filesystem, so the admission check measures the storage PostgreSQL actually uses. Set disk alerts before admitting public queries.
4. Check out the reviewed application commit into `/opt/research-workspace`. Keep this path because the supplied systemd units refer to it. Supply the final API DNS hostname and point it to this host before using public HTTPS.
5. Run initialization as root. It generates six different secrets and never overwrites an existing installation.

```bash
cd /opt/research-workspace/projects/research-workspace
sudo python3 deploy/manage.py init --hostname api.example.com
sudo python3 deploy/manage.py check
sudo python3 deploy/manage.py up
sudo python3 deploy/manage.py status
```

`config.env` contains only settings and the exact application commit. Secrets live in `/etc/research-workspace/secrets`; Compose mounts only the files each service needs. The files belong to root with group 10001 and mode 0440. The application runs as UID/GID 10001. Keep these files outside Git and preserve those permissions. [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)

The bootstrap uses a PostgreSQL administrator only for migrations and role setup. It creates separate API, worker, importer, and backup logins. The API and worker inherit their existing restricted groups. The importer has source-data write grants and RLS bypass; the backup login has RLS bypass and read grants. Neither importer nor backup is a superuser or can create roles or databases. RLS bypass belongs to the login itself because it is not inherited from a group.

## Deployment and updates

`up` first verifies that `APP_COMMIT` equals the checked-out Git commit and that the backend build context has no staged, unstaged, or untracked changes. It then builds the service and operator images, waits for PostgreSQL, stops API and worker writes, applies migrations, and starts the services. The SQL sequence is 001, 002, 003, service 002, 005, and 006. Supabase storage migration 004 is excluded. Local rehearsal explicitly allows uncommitted files and labels its images/report `uncommitted-local-rehearsal`; such an image is not a reviewed production release.

The migration runner holds a database advisory lock. It checks the SHA-256 of normalized UTF-8/LF migration contents against `public.research_schema_migrations`. Schema changes, role setup, and ledger updates commit in one transaction. An altered applied migration or a migration missing from the selected release stops the operation. A failed migration leaves the previous database and publication state intact; inspect the failure before restarting the previous compatible services. It does not silently adopt an existing manually migrated database without a ledger.

Repeating `up` does not duplicate roles or reapply migrations. Password changes in the protected secret files take effect when `up` reruns role setup. A restart preserves the bind-mounted database and objects:

```bash
sudo python3 deploy/manage.py restart
```

Service health has two separate meanings. API health verifies database connectivity. Worker health verifies a recent process heartbeat, a live child process, and its database read permission. A running process is not proof of search progress; also monitor queue age, expired leases, and completed/failed/resource-limited jobs. A hung solver can remain process-healthy until its execution or lease limits take effect.

Caddy supplies HTTPS and replaces incoming forwarded-IP headers. Uvicorn trusts only the proxy's fixed address on the private ingress network. The request limit is 16 KiB. The API enforces ordinary-read, network, session, and queue limits. The ingress subnet is `172.29.80.0/24`; if the host already uses it, change the subnet, fixed proxy address, and API trust address together before deployment. [Compose startup ordering](https://docs.docker.com/compose/how-tos/startup-order/), [Caddy proxy headers](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)

## Import source data

Prepare a verified source bundle on the operator's machine using the existing importer. Transfer its contents into `/srv/research-workspace/bundle`, readable by UID 10001. Do not put the source project or credentials in the container image.

```bash
sudo python3 deploy/manage.py import
```

The importer uses its dedicated login and writes immutable objects into `/srv/research-workspace/objects`. The API and ordinary worker do not mount those objects. Source files remain private recovery inputs. Restoring a deployment backup instead requires a fresh isolated target and the backup runbook; do not run `pg_restore --clean` against this installation.

## Configure private R2 backups

Write the following JSON structure into `/etc/research-workspace/secrets/r2_config` with real values, retaining its protected permissions:

```json
{
  "R2_BUCKET": "REPLACE_WITH_PRIVATE_BUCKET",
  "R2_ENDPOINT": "https://REPLACE_WITH_ACCOUNT.r2.cloudflarestorage.com",
  "R2_ACCESS_KEY_ID": "REPLACE_WITH_BUCKET_SCOPED_KEY",
  "R2_SECRET_ACCESS_KEY": "REPLACE_WITH_SECRET",
  "RESEARCH_BACKUP_PREFIX": "research-backups"
}
```

The backup container receives this file and its dedicated read-only database login. API and worker containers receive neither. Run a manual backup and inspect its sanitized report before enabling a schedule:

```bash
sudo python3 deploy/manage.py backup
```

The report and private staging files are under `/srv/research-workspace/backup-work`. The packaged backup command uses `--discard-staging` after remote verification so successful daily backups retain only small local metadata and reports. Failed uploads retain their partial local payload for inspection and disk monitoring. Follow `ops/backup-README.md` for listing, verification, isolated restore, and retention. Measure stored bytes and request operations before scheduling. Remote pruning is deliberately separate from the daily upload, and its default is a dry run.

## Recover an existing backup on a fresh host

Use a fresh installation with its own empty state directory. Initialize its secrets and config, then set `PGDATABASE=research_recovered_restore_test` in that config before starting PostgreSQL. The name must differ from the source database and end in `_restore_test`. Supply private R2 settings as above. Do not run `up` first: the restore requires an empty database, without migrations or a connected API.

```bash
sudo python3 deploy/manage.py restore --backup-id BACKUP_ID
```

The command builds the selected clean source, starts only the database, downloads and verifies the backup in an isolated operator container, and restores the database. It then provisions service roles and passwords from the new secret files. It promotes only the verified restored object directory into the empty application's object location and starts the API and worker. The restore operation does not start a scheduler. Reopen the original record, statistics, and saved-query URLs against the recovered API and record end-to-end recovery time before promotion.

Backups from the original local application have no migration ledger. For those backups only, this flow invokes explicit catalog adoption before migration. `schema-baselines.json` contains fingerprints generated from clean PostgreSQL 17 schemas through migration 005 and through 006. Adoption compares relations, visible columns, views, constraints, indexes, policies, grants, functions, sequence definitions, triggers, and domain/enum types. It also verifies the packaged migration file hashes. Data and current sequence values are excluded because they change during ordinary use.

One exact legacy constraint, `datasets_latest_publication_id_fkey` on `research.datasets`, is retained but excluded from comparison. Its single-column reference to `research.publications(id)` is redundant with the required composite `dataset_latest_scope` constraint. Any other catalog difference rejects adoption without creating a ledger. Adoption itself does not change cluster roles, source rows, or existing constraints. The subsequent normal migration provisions only the deployment's designated service logins. Existing backups with a ledger use the ordinary checksum check instead.

For an already restored isolated database, `python3 deploy/manage.py adopt` performs only that explicit comparison and ledger creation. Then `up` applies pending migrations and configures logins. If adoption fails, inspect the difference and add a reviewed migration or reference profile; never stamp files as applied merely because their names exist.

## Schedule operations

The cleanup and backup jobs are separate systemd services. A host file lock prevents scheduled maintenance from overlapping a deployment or another maintenance command. Backup also protects its object inventory against application cleanup through the backup module's advisory-lock protocol.

Create an executable, root-owned `/etc/research-workspace/notify-failure` that delivers an operator-visible failure notification through the operator's chosen channel. Its argument is either a sanitized JSON report path or the failed systemd unit name. It must handle both and return nonzero if delivery fails. Test it manually. No notification destination or credentials are built into this repository.

Only enable the backup timer after a successful manual backup, an isolated restore drill, a cost/retention review, and a working notifier. Cleanup can be enabled separately.

```bash
sudo cp deploy/systemd/research-*.service deploy/systemd/research-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start research-cleanup.service
sudo systemctl start research-backup.service
sudo systemctl enable --now research-cleanup.timer research-backup.timer
sudo systemctl list-timers 'research-*'
```

Timers use UTC explicitly and run missed jobs after restart. Backup runs daily at 03:00 UTC and cleanup at 02:00 UTC, each with up to five minutes of jitter. `journalctl -u research-backup.service` and `reports/scheduled-backup.json` expose failures. `OnFailure` covers configuration failures that occur before the backup command runs. Reports contain exception categories, never credentials or connection strings. The backup report is authoritative for backup age, size, and verification; a successful scheduler invocation alone is not a restore drill.

## Capacity and rollback

Configuration exposes worker concurrency, query limits, retention, database/API/worker memory and CPU limits, and minimum free disk. Defaults start one worker with a two-GiB disk reserve. Low disk rejects new work and stops result allocation without deleting source publications or frozen rules. Tune from actual measurements; these defaults do not waive the two-worker hosted acceptance test. Set an alert above the admission threshold so operator action precedes rejection.

An update records built image IDs in `reports/release-images.json`. Retain this report, the Git commit, image copies, migration ledger, and a verified backup. For application rollback, select a previously verified commit from this download-disabled deployment series, update `APP_COMMIT`, and rerun `up` with the same state and secret paths. The selected release must include every applied migration with identical hashes. The runner rejects incompatible schema history. Never roll back to a commit that restores public downloads; the proxy's explicit retired-route denial is additional protection, not an alternative to the application contract. The first release in this series has no earlier eligible rollback image.

Do not remove volumes, delete the state directory, or revert SQL migrations for an interface/API rollback. If the current schema needs an incompatible rollback, retain the installation and use the separately reviewed recovery procedure on an isolated target.

## Local Linux rehearsal

Use a disposable Linux host or VM with Docker. Initialization generates fresh credentials. A separate config and state directory prevent accidental use of production data. The local overlay exposes only loopback port 8879 and uses plain HTTP; it never requests a public TLS certificate.

```bash
sudo python3 deploy/manage.py --config /tmp/research-rehearsal/config.env init --state /tmp/research-rehearsal/state --hostname http://localhost --origin http://127.0.0.1:4330
sudo python3 deploy/manage.py --config /tmp/research-rehearsal/config.env --local check
sudo python3 deploy/manage.py --config /tmp/research-rehearsal/config.env --local up
curl --fail http://localhost:8879/health
sudo python3 deploy/manage.py --config /tmp/research-rehearsal/config.env --local up
sudo python3 deploy/manage.py --config /tmp/research-rehearsal/config.env --local restart
```

Import a named snapshot and run a saved query before restart; reopen both afterward and compare the same identities/counts. Test a failed migration on a separate disposable database. Record architecture, runtime versions, images, commit, timings, and failed/pending checks. Do not describe this rehearsal as complete until those commands and actual browser/API behavior have been observed.

## Packaged checks and base image pins

Run `python -m unittest deploy.test_deploy -v` from the backend directory. To include the transactional integration test, set `RESEARCH_DEPLOY_TEST_DATABASE_URL` to a fresh database ending in `_deploy_test` on a disposable PostgreSQL cluster. The test creates global role definitions, so sharing a cluster with live application logins is inappropriate.

The Docker registry was queried on November 23, 2026. The pinned indexes include amd64 and arm64 manifests:

| Image | Index SHA-256 |
| --- | --- |
| Python 3.12 slim Bookworm | `935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df` |
| PostgreSQL 17 Bookworm | `837bed18782820fe967067cb5328b02b0d9992c1be9a37ee0c9c492cfc03af3b` |
| Caddy 2 Alpine | `003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0` |

The operator build installs Bookworm runtime libraries and the pinned backup Python requirements. Archive the final built image IDs because package repository updates can change a later rebuild. Base-image pins alone do not guarantee identical rebuilt application bytes. Update pins and rerun the build, migration, recovery, and route checks together.
