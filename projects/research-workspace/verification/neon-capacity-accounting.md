# Neon branch capacity qualification

Reviewed 2026-11-25. The opt-in `neon_branch` collector measures the current
branch's logical relation storage against its enforced size limit. It does not
measure an account invoice, another branch, restore-history storage, or provider
physical disk allocation.

Neon's branch documentation recommends summing `pg_database_size` over all
databases to inspect branch data size, and distinguishes that size from history.
The Free plan separately includes a history window of up to six hours or 1 GB of
changes. The deployment remains on Free; changing the plan or branch topology
requires another review. [Branch data size](https://neon.com/docs/manage/branches#check-the-data-size),
[Free-plan limits](https://neon.com/docs/introduction/plans).

The PostgreSQL 17 fork's `calculate_database_size` invokes `dbsize_hook` and
explicitly excludes shared catalogs. Neon's `neon_dbsize` sends a pageserver
request at the current request LSN. The pageserver obtains database relation
block counts from shard zero. Its branch logical-size calculation sums those
relation blocks across database directories. This is the implementation basis
for summing every database, including templates and the restore database, then
adding each shared relation's main, FSM, VM and init forks once. Indexes are
included as relations; `pg_total_relation_size` would double count them.
[PostgreSQL fork](https://github.com/neondatabase/postgres/blob/REL_17_STABLE_neon/src/backend/utils/adt/dbsize.c),
[Neon storage client](https://github.com/neondatabase/neon/blob/main/pgxn/neon/pagestore_smgr.c),
[Pageserver requests](https://github.com/neondatabase/neon/blob/main/pageserver/src/page_service.rs),
[Logical-size accounting](https://github.com/neondatabase/neon/blob/main/pageserver/src/pgdatadir_mapping.rs).

A bounded, read-only, TLS-verified probe of the deployed branch reported
PostgreSQL `17.12 (82f091e)`, registered `neon.max_cluster_size=1024 MB`, five
databases, no custom tablespaces, 249,995,264 database bytes and 794,624 shared
relation bytes: 250,789,888 bytes total. These are observations from that probe,
not a permanent capacity baseline. The public source inspection is not proof
that the deployed build matches a particular public commit. The `neon` SQL
extension and `pg_cluster_size()` were absent; the collector does not depend on
installing them. The latter would read cached pageserver feedback anyway.
[Feedback implementation](https://github.com/neondatabase/neon/blob/main/pgxn/neon/walproposer_pg.c).

Migration 011 permits only the API role to invoke a fixed `SECURITY DEFINER`
collector. Its implementation owner needs access to database size statistics;
the API receives no owner password or management API key. The collector requires
registered PostgreSQL 17 Neon settings, the preloaded Neon library, standard
tablespaces and at most 16 databases. Its ceiling is the lower of the live GUC
and the reviewed setting, which cannot exceed 1 GiB. Missing permissions, an
unsupported provider, changed database inventory or failed measurement stops
new admission. The default remains `manual`.

Only new search admission calls the collector; catalog reads, cached searches
and idle dispatch polling do not. The measurement start time is recorded before
reading sizes. Reservations for active work remain charged even if some writes
are already included. Only reservations already terminal and without an
outstanding execution before measurement can be released afterward. This avoids
mistaking a transaction's older `finished_at` for its commit time. Capacity is
serialized with query admission and protected by the existing extra headroom.

The management API is unsuitable as a fresh Free-plan admission source:
`synthetic_storage_size` is deprecated and always zero, branch `updated_at` is
not documented as a measurement timestamp, and there is no public force-refresh
size endpoint. Consumption-history v2 is for paid plans, uses hourly or larger
buckets, and updates about every 15 minutes. If a separate metadata-only
collector is later needed, a dedicated Collaborator with a project Viewer grant
and a personal API key inherits read-only access; project-scoped organization
keys instead have Editor access. No such key is needed or provisioned here.
[Official API schema](https://neon.com/api_spec/release/v2.json),
[Consumption metrics](https://neon.com/docs/guides/consumption-metrics),
[User permissions](https://neon.com/docs/manage/user-permissions),
[API-key scopes](https://neon.com/docs/manage/api-keys).
