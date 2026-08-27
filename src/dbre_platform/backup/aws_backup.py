"""AWS RDS-native backup configuration and operator helpers.

Unlike ``dbre_platform.backup.local_backup`` (which runs ``pg_dump``/
``pg_restore`` itself), RDS backups are a *managed* mechanism: automated
snapshots taken on the schedule and retention window configured directly
on the DB instance (``backup_retention_days`` -> ``aws_db_instance.
backup_retention_period`` -- see
``terraform/modules/rds_postgresql/main.tf``). This module does not
re-implement that; it exposes the derived configuration as a pure,
testable function, plus thin operational helpers for on-demand snapshots
and snapshot-based restores where boto3 and real AWS credentials are
available.

**Exercised vs not exercised** (see ``docs/local-vs-aws.md``):
``build_backup_config`` is a pure function with no AWS dependency and is
unit tested. ``SnapshotManager`` requires boto3 (an optional extra --
``pip install dbre-platform[aws]``) and was written and reviewed
carefully but **not run against a real AWS account**, for the same
reason as ``dbre_platform.provisioning.aws_rds``: no AWS credentials were
available while building this repository. Treat it as ready for a real
account, not proven against one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.exceptions import BackupError

try:
    import boto3  # type: ignore[import-not-found]
    from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-not-found]

    _BOTO3_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when boto3 is absent
    _BOTO3_AVAILABLE = False


@dataclass(frozen=True)
class BackupConfig:
    """The RDS-native backup configuration implied by a request.

    These values must match what ``terraform/modules/rds_postgresql``
    actually applies -- ``backup_window``/``maintenance_window`` are fixed
    there rather than per-request, since staggering real production
    maintenance windows across a fleet of databases is a platform-level
    decision, not one a single self-service request should be allowed
    to make.
    """

    retention_days: int
    backup_window_utc: str
    maintenance_window_utc: str
    copy_tags_to_snapshot: bool
    deletion_protection: bool
    final_snapshot_on_delete: bool


def build_backup_config(request: DatabaseRequest) -> BackupConfig:
    """Derive the RDS-native backup configuration for a request.

    Pure function, deliberately mirroring
    ``dbre_platform.provisioning.aws_rds.build_tfvars`` -- no I/O, so it
    is fully unit testable without AWS credentials or Terraform.
    """
    return BackupConfig(
        retention_days=request.spec.backup_retention_days,
        backup_window_utc="03:00-04:00",
        maintenance_window_utc="sun:04:30-sun:05:30",
        copy_tags_to_snapshot=True,
        deletion_protection=request.spec.deletion_protection,
        final_snapshot_on_delete=request.metadata.environment == "prod",
    )


@dataclass(frozen=True)
class SnapshotInfo:
    snapshot_id: str
    db_instance_id: str
    status: str
    created_at: Optional[str]
    snapshot_type: str


def _require_boto3() -> None:
    if not _BOTO3_AVAILABLE:
        raise BackupError(
            "boto3 is not installed. Install with `pip install dbre-platform[aws]` "
            "to use AWS snapshot operations, or use the local backup/DR-test path "
            "(dbre_platform.backup.local_backup, dbre_platform.backup.dr_test) which "
            "requires no AWS account."
        )


class SnapshotManager:
    """Thin boto3 wrapper for on-demand RDS snapshot operations.

    Every public method raises ``BackupError`` (never a bare boto3/botocore
    exception) so callers -- especially the CLI -- get one exception type
    to catch, matching every other executor in this platform.
    """

    def __init__(self, region_name: Optional[str] = None) -> None:
        _require_boto3()
        self._client = boto3.client("rds", region_name=region_name)

    def create_manual_snapshot(self, db_instance_id: str, snapshot_id: str) -> SnapshotInfo:
        try:
            response = self._client.create_db_snapshot(
                DBInstanceIdentifier=db_instance_id, DBSnapshotIdentifier=snapshot_id
            )
        except (BotoCoreError, ClientError) as exc:
            raise BackupError(f"failed to create snapshot {snapshot_id!r}: {exc}") from exc
        return _snapshot_info_from(response["DBSnapshot"])

    def list_snapshots(self, db_instance_id: str) -> list[SnapshotInfo]:
        try:
            response = self._client.describe_db_snapshots(DBInstanceIdentifier=db_instance_id)
        except (BotoCoreError, ClientError) as exc:
            raise BackupError(f"failed to list snapshots for {db_instance_id!r}: {exc}") from exc
        return [_snapshot_info_from(item) for item in response.get("DBSnapshots", [])]

    def restore_snapshot_to_new_instance(
        self,
        snapshot_id: str,
        new_db_instance_id: str,
        *,
        instance_class: str = "db.t4g.micro",
    ) -> str:
        """Restore a snapshot to a brand-new instance -- the AWS analogue of
        ``dbre_platform.backup.dr_test``'s restore-to-throwaway-database
        step. Returns the new instance identifier immediately: unlike the
        synchronous local DR test, RDS restores are asynchronous and take
        several minutes, so callers must separately poll
        ``describe_db_instances`` for ``available`` status before running
        verification queries against it.
        """
        try:
            self._client.restore_db_instance_from_db_snapshot(
                DBInstanceIdentifier=new_db_instance_id,
                DBSnapshotIdentifier=snapshot_id,
                DBInstanceClass=instance_class,
                PubliclyAccessible=False,
            )
        except (BotoCoreError, ClientError) as exc:
            raise BackupError(f"failed to restore snapshot {snapshot_id!r}: {exc}") from exc
        return new_db_instance_id


def _snapshot_info_from(payload: dict) -> SnapshotInfo:
    created = payload.get("SnapshotCreateTime")
    return SnapshotInfo(
        snapshot_id=payload["DBSnapshotIdentifier"],
        db_instance_id=payload["DBInstanceIdentifier"],
        status=payload["Status"],
        created_at=str(created) if created else None,
        snapshot_type=payload.get("SnapshotType", "manual"),
    )
