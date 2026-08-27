"""Audit event logging.

Every platform action -- validate, provision, backup, restore, DR test,
readiness assessment -- writes exactly one audit event via this module. The
requirement (see README / SECURITY.md) is simple: "every platform action
should generate an audit event," so nothing about who requested what,
against which environment, with what outcome, is ever reconstructed after
the fact from memory or a Slack thread.

Events are appended as JSON Lines (one JSON object per line) to a local file
by default (``./audit-log/audit.jsonl``), which is what the local demo uses.
In a real deployment this file would instead be a sink that ships to
CloudWatch Logs / a SIEM; swapping the sink is a constructor argument, not a
rewrite (see ``AuditLogger.__init__``).

Each event includes a SHA-256 hash of its own canonical content chained to
the previous event's hash (git-commit style), so ``AuditLogger.verify_chain``
can detect if a line was edited or deleted after the fact -- a cheap,
dependency-free way to make the log tamper-evident without standing up a
separate integrity service.
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

DEFAULT_AUDIT_LOG = Path("audit-log/audit.jsonl")
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
        """The exact byte content that gets hashed -- everything except the
        hash of this event itself (which would be circular)."""
        payload = asdict(self)
        payload.pop("event_hash")
        return json.dumps(payload, sort_keys=True, default=str)

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)


class AuditLogger:
    """Appends tamper-evident audit events to a JSON Lines file."""

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
        event = AuditEvent(
            action=action,
            target=target,
            environment=environment,
            outcome=outcome,
            actor=actor or os.environ.get("DBRE_ACTOR", getpass.getuser()),
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
        events = []
        for line in self.log_path.read_text().splitlines():
            if line.strip():
                events.append(AuditEvent(**json.loads(line)))
        return events

    def verify_chain(self) -> tuple[bool, list[str]]:
        """Recompute every event's hash and confirm the chain is intact.

        Returns ``(is_valid, problems)``. This is a demonstration of
        tamper-evidence, not a substitute for shipping the log to
        write-once storage -- see docs/decisions/0005-audit-log-integrity.md.
        """
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
