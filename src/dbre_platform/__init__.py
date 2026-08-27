"""DBRE Platform: a self-service PostgreSQL database platform.

This package implements the control-plane logic described in the project
README: a developer submits a declarative database request, the request is
validated by a policy engine, and -- only if it passes -- the platform
provisions a PostgreSQL environment (locally via Docker, or in AWS via
Terraform + RDS) with DBRE standards, RBAC, audit logging, observability,
and operational readiness gating already applied.

See docs/architecture.md for the full request lifecycle.
"""

from dbre_platform.version import __version__

__all__ = ["__version__"]
