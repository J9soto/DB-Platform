"""Exception hierarchy for the Change Risk Engine.

Mirrors the shape of ``dbre_platform.exceptions`` (a small, specific
hierarchy rooted at one base class per package) deliberately -- it's a
convention worth repeating, not worth importing across the domain
boundary. See docs/decisions/0008-change-risk-engine-domain-separation.md.
"""

from __future__ import annotations


class ChangeRiskEngineError(Exception):
    """Base class for every error this package raises."""


class ParseError(ChangeRiskEngineError):
    """A submitted change could not be parsed into structured operations."""


class MetadataUnavailableError(ChangeRiskEngineError):
    """Database metadata could not be collected (connection, permissions, missing binary)."""


class ConnectorError(ChangeRiskEngineError):
    """A ``DatabaseConnector`` failed to execute a read-only metadata query."""


class PolicyError(ChangeRiskEngineError):
    """A risk policy file is missing, malformed, or references an unknown field."""


class StoreError(ChangeRiskEngineError):
    """The assessment store could not read or write a record."""


class NotFoundError(ChangeRiskEngineError):
    """A requested change, assessment, or related record does not exist."""


class UnsupportedChangeTypeError(ChangeRiskEngineError):
    """No registered ``ChangeAnalyzer`` can handle the submitted change type."""


class AuthorizationError(ChangeRiskEngineError):
    """The caller is not permitted to perform the requested action."""
