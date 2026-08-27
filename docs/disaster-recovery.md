# Disaster recovery

## The principle: an untested backup is a hypothesis, not a recovery plan

Every DBRE team has a story about a backup that turned out to be
unrestorable exactly when it mattered. The only way to know a backup
strategy actually works is to actually restore from it, on a schedule,
before an incident forces the question. `dbre_platform.backup.dr_test`
exists specifically to make that a one-command, script-repeatable check
instead of a rare, manual, and often-skipped exercise.

## What `dr_test.run_dr_test` actually does

This is a real drill, not a mock -- it genuinely runs `pg_dump`, restores
into a throwaway database, and runs verification queries against the
restored copy:

1. **Create backup** -- a real `pg_dump -Fc` of the source database
   (`dbre_platform.backup.local_backup.LocalBackupManager.create_backup`).
2. **Verify backup checksum** -- recomputes the sha256 of the dump file
   and compares it to the one recorded at backup time, catching silent
   corruption (a truncated copy, a disk error) before a restore is ever
   attempted.
3. **Create restore target database** -- a fresh, uniquely-named database
   (`dbre_dr_test_<random>`) on the same server, so the drill never
   touches the original data.
4. **Restore backup** -- a real `pg_restore` into that throwaway
   database.
5. **Run verification queries** -- caller-supplied SQL run against the
   *restored* copy (default: `SELECT 1`, just confirming the database is
   queryable at all; a real application should pass something like
   `SELECT count(*) FROM orders` so the drill validates its actual data,
   not just that some database exists).
6. **Clean up** -- the throwaway database is dropped whether the drill
   passed or failed (a `try`/`finally`), so a failed drill doesn't leave
   debris behind.

Every step's pass/fail and timing is recorded in a `DrTestResult`; a
single failed step stops the drill immediately (except cleanup, which
always runs) and the result is reported as `FAILED`, with explicit
language that a failed drill should be treated like a production
incident, not a test-suite red X.

This was verified end to end against a real, running PostgreSQL 16
server during development, including the failure path (bad credentials
fail at the backup step, cleanly, with no half-created throwaway
database left behind) and the "restore succeeds but data doesn't
match" path (a bad verification query fails the drill while cleanup
still runs).

## Scope: what this drill proves, and what it doesn't

Restoring onto the *same* server, rather than a genuinely separate
standby, is a deliberate scope limit so the local/demo path needs no
second PostgreSQL instance. It proves: the backup artifact exists, its
checksum is intact, `pg_restore` can rebuild a working database from it,
and the specific data you asked it to check about is present and correct
in the restored copy.

It does **not** prove: failover behavior, recovery time under a real
regional or AZ outage, application-level correctness beyond whatever
verification queries were supplied, or cross-region/cross-account
recovery. A production DR program built on this platform should
additionally: restore to a genuinely separate target (a different host,
region, or account); measure wall-clock recovery time and compare it
against the request's `recovery_rto_hours` SLO target
(`dbre_platform.slo`); and run application-level smoke tests against the
restored environment, not just SQL queries.

## AWS mode

RDS backups are a managed mechanism (automated snapshots on the schedule
and retention window Terraform configures via
`backup_retention_days` -- see `dbre_platform.backup.aws_backup` and
`docs/local-vs-aws.md`). The equivalent AWS-side DR drill is: take or
identify a snapshot, restore it to a new instance
(`SnapshotManager.restore_snapshot_to_new_instance`), wait for it to
become available (RDS restores are asynchronous, unlike the synchronous
local drill), then run the same kind of verification queries against it.
That flow was written and reviewed carefully but not exercised against a
real AWS account -- see the disclosure in
`dbre_platform.backup.aws_backup`'s module docstring.

## Using it

```
dbre dr-test run orders-api-prod --dbname orders_api --query "SELECT count(*) FROM orders"
```

Exits non-zero on any failed step, so this is suitable as a scheduled
CI/cron check whose failure should page whoever owns the database, not
just fail silently in a log nobody reads.
