"""Exception hierarchy for the DBRE platform.

Every operation-affecting failure in the platform raises a subclass of
``DBREPlatformError`` rather than a bare ``Exception`` or ``ValueError``, so
callers (the CLI, tests, and any future API layer) can catch platform errors
specifically and print operator-friendly messages instead of stack traces.
"""

from __future__ import annotations


class DBREPlatformError(Exception):
    """Base class for all errors raised by the DBRE platform."""


class ConfigurationError(DBREPlatformError):
    """The database request file is malformed or fails schema validation."""


class PolicyViolationError(DBREPlatformError):
    """A database request failed one or more policy rules.

    Raised by callers that need validation failures to be fatal (for example,
    ``dbre provision`` refusing to continue). The policy engine itself returns
    a ``PolicyResult`` rather than raising, so callers that only want to
    *report* violations (``dbre validate``) are not forced into exception
    handling.
    """


class ProvisioningError(DBREPlatformError):
    """Provisioning a database environment failed."""


class ExecutorError(DBREPlatformError):
    """Executing SQL or a subprocess (psql, docker, terraform) failed."""


class ReadinessError(DBREPlatformError):
    """The operational readiness score is below the configured threshold."""


class BackupError(DBREPlatformError):
    """A backup or restore operation failed."""


class CapacityDataError(DBREPlatformError):
    """Historical capacity data is missing, malformed, or insufficient."""
