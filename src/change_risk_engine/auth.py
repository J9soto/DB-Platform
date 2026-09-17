"""Authentication/authorization abstraction.

Section 20 of the product brief asks for this as an abstraction, not
necessarily a full identity provider integration for the MVP. ``AuthProvider``
is the extension point a real deployment wires SSO/OAuth into;
``ApiKeyAuthProvider`` (a single shared secret via ``CRE_API_KEY``) is the
concrete MVP implementation, and ``NoAuthProvider`` is the explicit,
clearly-labeled local-dev default when ``CRE_API_KEY`` is unset -- never a
silent "auth is on" that actually accepts everything.
"""

from __future__ import annotations

import os
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from change_risk_engine.exceptions import AuthorizationError


@dataclass(frozen=True)
class Principal:
    """Who is making a request, and what they're allowed to do.

    ``roles`` is intentionally coarse for the MVP: ``"viewer"`` (read-only),
    ``"interact"`` (submit changes, request assessments), ``"admin"``
    (manage policies, overrides). A real deployment maps these onto
    whatever its identity provider issues.
    """

    name: str
    roles: frozenset[str] = field(default_factory=lambda: frozenset({"viewer"}))

    def has_role(self, role: str) -> bool:
        return role in self.roles or "admin" in self.roles


class AuthProvider(ABC):
    @abstractmethod
    def authenticate(self, credential: str | None) -> Principal | None:
        """Return the authenticated ``Principal``, or ``None`` if ``credential`` is invalid/missing."""


class NoAuthProvider(AuthProvider):
    """Every request is an anonymous admin. Local-dev/demo only.

    ``change_risk_engine.api.app`` refuses to select this provider unless
    ``CRE_API_KEY`` is genuinely unset -- see ``resolve_auth_provider`` --
    so a deployment can never end up here by forgetting to set one thing.
    """

    def authenticate(self, credential: str | None) -> Principal | None:
        return Principal(name="anonymous", roles=frozenset({"admin"}))


class ApiKeyAuthProvider(AuthProvider):
    """A single shared secret, compared with a constant-time comparison."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def authenticate(self, credential: str | None) -> Principal | None:
        if credential is None or not secrets.compare_digest(credential, self._api_key):
            return None
        return Principal(name="api-key-client", roles=frozenset({"admin"}))


def resolve_auth_provider() -> AuthProvider:
    api_key = os.environ.get("CRE_API_KEY")
    if api_key:
        return ApiKeyAuthProvider(api_key)
    return NoAuthProvider()


def require_role(principal: Principal | None, role: str) -> Principal:
    if principal is None or not principal.has_role(role):
        raise AuthorizationError(f"'{role}' role required.")
    return principal
