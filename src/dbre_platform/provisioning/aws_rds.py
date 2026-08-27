"""AWS provisioning mode: Terraform + RDS PostgreSQL.

This is the production-oriented path -- see docs/local-vs-aws.md for
exactly how it differs from local Docker mode, and
docs/decisions/0006-terraform-assumes-existing-vpc.md for why this module
takes VPC/subnet IDs as input rather than creating networking itself.

This module was written and reviewed carefully, but was **not exercised
against a real AWS account** as part of building this repository -- no AWS
credentials or Terraform binary were available in the environment used to
build it. What *was* verified: the Terraform HCL is hand-checked for
correctness against the AWS provider's documented resource schema, and the
tfvars-generation logic below has unit test coverage
(tests/unit/test_aws_provisioner.py) proving it produces the JSON structure
Terraform expects from a given DatabaseRequest. Treat the Terraform side of
this path as "ready for a real `terraform plan` against your account," not
as "has been applied in production."
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.exceptions import ProvisioningError
from dbre_platform.logging_config import get_logger
from dbre_platform.postgres.standards import build_cluster_parameters
from dbre_platform.provisioning.base import ProvisionResult, Provisioner
from dbre_platform.tagging.governance import resolve_tags

logger = get_logger("provisioning.aws_rds")

DEFAULT_TERRAFORM_ROOT = Path("terraform/environments")


def build_tfvars(request: DatabaseRequest) -> dict:
    """Translate a validated DatabaseRequest into Terraform variables.

    Pure function, deliberately -- it takes a request and returns a dict,
    with no filesystem or subprocess side effects, so it can be unit tested
    without Terraform being installed at all.
    """
    cluster_params = build_cluster_parameters(request)
    return {
        "name": request.metadata.name,
        "engine_version": f"{request.spec.engine_version}.4",
        "instance_class": request.spec.instance_class,
        "allocated_storage_gb": request.spec.storage_gb,
        "backup_retention_days": request.spec.backup_retention_days,
        "connection_limit": request.spec.connection_limit,
        "cluster_parameters": cluster_params.values,
        "tags": resolve_tags(request),
        # dev-only knobs; prod/main.tf hardcodes the safe values and ignores
        # these if passed, so a dev tfvars file can never weaken prod.
        "multi_az": request.spec.multi_az,
        "deletion_protection": request.spec.deletion_protection,
        "enhanced_monitoring": request.spec.enhanced_monitoring,
    }


class AwsRdsProvisioner(Provisioner):
    """Drives `terraform` against ``terraform/environments/<environment>/``.

    Requires the Terraform CLI and valid AWS credentials in the environment
    (standard `AWS_PROFILE` / `AWS_ACCESS_KEY_ID` resolution -- this class
    never reads or handles AWS credentials directly). Networking
    (``vpc_id``, ``subnet_ids``, ``allowed_security_group_ids``) must
    already exist in ``terraform.tfvars`` -- see terraform.tfvars.example
    in each environment directory.
    """

    def __init__(self, *, terraform_root: Path = DEFAULT_TERRAFORM_ROOT, auto_approve: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.terraform_root = terraform_root
        self.auto_approve = auto_approve

    def _env_dir(self, environment: str) -> Path:
        env_dir = self.terraform_root / environment
        if not env_dir.exists():
            raise ProvisioningError(f"No Terraform environment found at {env_dir}")
        return env_dir

    def _write_generated_tfvars(self, env_dir: Path, request: DatabaseRequest) -> Path:
        generated = env_dir / f"{request.metadata.name}.auto.tfvars.json"
        generated.write_text(json.dumps(build_tfvars(request), indent=2))
        return generated

    def _run_terraform(self, env_dir: Path, *args: str) -> subprocess.CompletedProcess:
        if shutil.which("terraform") is None:
            raise ProvisioningError(
                "`terraform` was not found on PATH. Install Terraform >= 1.5 to use AWS mode, "
                "or use --mode local for the no-AWS-account demo path."
            )
        logger.info("running terraform", extra={"args": args, "cwd": str(env_dir)})
        return subprocess.run(
            ["terraform", *args],
            cwd=env_dir,
            capture_output=True,
            text=True,
            timeout=600,
        )

    def _provision(self, request: DatabaseRequest) -> ProvisionResult:
        env_dir = self._env_dir(request.metadata.environment)
        tfvars_path = self._write_generated_tfvars(env_dir, request)

        init_result = self._run_terraform(env_dir, "init", "-input=false")
        if init_result.returncode != 0:
            raise ProvisioningError(f"terraform init failed:\n{init_result.stderr}")

        plan_result = self._run_terraform(
            env_dir, "plan", "-input=false", f"-var-file={tfvars_path.name}", "-out=tfplan"
        )
        if plan_result.returncode != 0:
            raise ProvisioningError(f"terraform plan failed:\n{plan_result.stderr}")

        if not self.auto_approve:
            return ProvisionResult(
                success=True,
                mode="aws",
                full_name=request.full_name(),
                connection_info={},
                credentials_location="(plan only - apply was not run; pass --yes to apply)",
                messages=[
                    "Terraform plan generated successfully but NOT applied "
                    "(AwsRdsProvisioner.auto_approve is False by default).",
                    plan_result.stdout[-4000:],  # tail of plan output
                ],
            )

        apply_result = self._run_terraform(env_dir, "apply", "-input=false", "-auto-approve", "tfplan")
        if apply_result.returncode != 0:
            raise ProvisioningError(f"terraform apply failed:\n{apply_result.stderr}")

        output_result = self._run_terraform(env_dir, "output", "-json")
        outputs = json.loads(output_result.stdout) if output_result.returncode == 0 else {}

        return ProvisionResult(
            success=True,
            mode="aws",
            full_name=request.full_name(),
            connection_info={
                "endpoint": outputs.get("endpoint", {}).get("value"),
                "port": 5432,
            },
            credentials_location=outputs.get("master_secret_arn", {}).get("value", "unknown"),
            messages=["terraform apply completed."],
        )
