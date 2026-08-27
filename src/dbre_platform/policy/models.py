"""Data types returned by the policy engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Violation:
    rule_id: str
    field: str
    message: str
    severity: Severity
    expected: Any
    actual: Any
    source: str = "policy"

    def as_line(self) -> str:
        marker = "ERROR" if self.severity is Severity.ERROR else "WARN "
        return f"[{marker}] {self.rule_id} ({self.field}): {self.message}"


@dataclass
class PolicyResult:
    """The outcome of evaluating a request against a policy set.

    ``passed`` reflects only ERROR-severity violations. WARNING-severity
    violations are surfaced but never block provisioning -- they exist so a
    platform team can introduce a new guardrail as a warning, watch adoption,
    and promote it to an error later without breaking every developer's
    request on day one.
    """

    passed: bool
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)
    evaluated_rules: int = 0

    def format_report(self) -> str:
        lines: list[str] = []
        if self.passed and not self.warnings:
            lines.append(f"PASSED - {self.evaluated_rules} policy rule(s) evaluated, no violations.")
        else:
            status = "PASSED WITH WARNINGS" if self.passed else "FAILED"
            lines.append(
                f"{status} - {self.evaluated_rules} rule(s) evaluated, "
                f"{len(self.violations)} error(s), {len(self.warnings)} warning(s)."
            )
            for violation in self.violations:
                lines.append("  " + violation.as_line())
            for violation in self.warnings:
                lines.append("  " + violation.as_line())
        return "\n".join(lines)
