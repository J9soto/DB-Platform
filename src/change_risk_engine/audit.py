"""Tamper-evident audit logging for the Change Risk Engine.

Same construction as ``dbre_platform.audit.AuditLogger`` (hash-chained
JSON Lines, git-commit style) -- not shared code, see
docs/decisions/0008-change-risk-engine-domain-separation.md -- applied to
CRE's own actions (submit, assess, override, deploy) in a separate log
file (default ``audit-log/cre-audit.jsonl``) so the two products' audit
trails never interleave.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_LOG = Path("audit-log") / "cre-audit.jsonl"
GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditEvent:
    action: str
    target: str
    environment: str
    outcome: str  # "success" | "failure" | "blocked"
    actor: str
    timestamp: str
    details: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = GENESIS_HASH
    event_hash: str = ""

    def canonical_payload(self) -> str:
        payload = asdict(self)
        payload.pop("event_hash")
        return json.dumps(payload, sort_keys=True, default=str)

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)


class AuditLogger:
    def __init__(self, log_path: str | Path = DEFAULT_AUDIT_LOG) -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        if not self.log_path.exists() or self.log_path.stat().st_size == 0:
            return GENESIS_HASH
        with self.log_path.open("rb") as fh:
            last_line = b""
            for line in fh:
                if line.strip():
                    last_line = line
            if not last_line:
                return GENESIS_HASH
            return json.loads(last_line)["event_hash"]

    def record(
        self,
        action: str,
        target: str,
        environment: str,
        outcome: str,
        *,
        actor: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        prev_hash = self._last_hash()
        resolved_actor = actor if actor is not None else os.environ.get("CRE_ACTOR", getpass.getuser())
        event = AuditEvent(
            action=action,
            target=target,
            environment=environment,
            outcome=outcome,
            actor=resolved_actor,
            timestamp=datetime.now(timezone.utc).isoformat(),
            details=details or {},
            prev_hash=prev_hash,
        )
        digest = hashlib.sha256(event.canonical_payload().encode("utf-8")).hexdigest()
        event = AuditEvent(**{**asdict(event), "event_hash": digest})
        with self.log_path.open("a") as fh:
            fh.write(event.to_json_line() + "\n")
        return event

    def read_all(self) -> list[AuditEvent]:
        if not self.log_path.exists():
            return []
        return [
            AuditEvent(**json.loads(line)) for line in self.log_path.read_text().splitlines() if line.strip()
        ]

    def verify_chain(self) -> tuple[bool, list[str]]:
        problems: list[str] = []
        expected_prev = GENESIS_HASH
        for index, event in enumerate(self.read_all()):
            if event.prev_hash != expected_prev:
                problems.append(f"line {index + 1}: prev_hash does not chain from prior event")
            recomputed = hashlib.sha256(event.canonical_payload().encode("utf-8")).hexdigest()
            if recomputed != event.event_hash:
                problems.append(f"line {index + 1}: event_hash does not match recomputed hash")
            expected_prev = event.event_hash
        return (len(problems) == 0, problems)
