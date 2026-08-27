"""The policy validation engine.

Loads declarative rule sets from YAML (global rules that apply to every
request, plus environment-specific guardrails) and evaluates a
``DatabaseRequest`` against them. This is the mandatory checkpoint in the
provisioning flow described in the README:

    developer -> self-service request -> DBRE CLI -> **policy engine** -> terraform / docker -> postgres

A request that fails policy is never provisioned -- see
``dbre_platform.provisioning.base.Provisioner.provision``, which calls this
engine before doing anything else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.exceptions import ConfigurationError
from dbre_platform.policy.models import PolicyResult, Severity, Violation
from dbre_platform.policy.rules import evaluate_operator, resolve_field_path

DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[3] / "policies"


class PolicyEngine:
    """Evaluates database requests against YAML-defined policy rules."""

    def __init__(self, policy_dir: str | Path = DEFAULT_POLICY_DIR) -> None:
        self.policy_dir = Path(policy_dir)
        self._global_rules: list[dict[str, Any]] = []
        self._env_rules: dict[str, list[dict[str, Any]]] = {}
        self._loaded = False

    # -- loading ----------------------------------------------------------

    def load(self) -> "PolicyEngine":
        """Load every policy file under ``policy_dir``. Idempotent."""
        if self._loaded:
            return self

        if not self.policy_dir.exists():
            raise ConfigurationError(f"Policy directory not found: {self.policy_dir}")

        # Global policies: any *.yaml directly under policy_dir (tagging.yaml,
        # naming.yaml, ...). These apply to every request regardless of
        # environment.
        for path in sorted(self.policy_dir.glob("*.yaml")):
            self._global_rules.extend(self._load_rule_file(path))

        # Environment policies: policies/environments/{dev,staging,prod}.yaml
        env_dir = self.policy_dir / "environments"
        if env_dir.exists():
            for path in sorted(env_dir.glob("*.yaml")):
                document = self._read_yaml(path)
                environment = document.get("environment") or path.stem
                self._env_rules[environment] = document.get("rules", [])

        self._loaded = True
        return self

    def _load_rule_file(self, path: Path) -> list[dict[str, Any]]:
        document = self._read_yaml(path)
        return document.get("rules", [])

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        try:
            content = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"Invalid policy YAML in {path}: {exc}") from exc
        if not isinstance(content, dict):
            raise ConfigurationError(f"Policy file {path} must contain a YAML mapping.")
        return content

    # -- evaluation ---------------------------------------------------------

    def rules_for(self, environment: str) -> list[dict[str, Any]]:
        """Return the full, ordered rule set that applies to an environment."""
        self.load()
        return [*self._global_rules, *self._env_rules.get(environment, [])]

    def evaluate(self, request: DatabaseRequest) -> PolicyResult:
        self.load()
        document = request.model_dump()
        rules = self.rules_for(request.metadata.environment)

        violations: list[Violation] = []
        warnings: list[Violation] = []

        for rule in rules:
            rule_id = rule.get("id", "<unnamed-rule>")

            condition = rule.get("when")
            if condition is not None:
                condition_actual = resolve_field_path(document, condition["field"])
                if not evaluate_operator(condition["operator"], condition_actual, condition.get("value")):
                    continue  # rule does not apply to this request

            field_path = rule["field"]
            operator = rule["operator"]
            expected = rule.get("value")
            severity = Severity(rule.get("severity", "error"))
            message = rule.get(
                "message",
                f"{field_path} must satisfy '{operator} {expected}'.",
            ).strip()

            actual = resolve_field_path(document, field_path)
            satisfied = evaluate_operator(operator, actual, expected)

            if not satisfied:
                try:
                    message = message.format(actual=actual, expected=expected, field=field_path)
                except (KeyError, IndexError, ValueError):
                    pass  # message had no substitutable placeholders (or bad ones) - use as-is
                violation = Violation(
                    rule_id=rule_id,
                    field=field_path,
                    message=message,
                    severity=severity,
                    expected=expected,
                    actual=actual,
                )
                if severity is Severity.ERROR:
                    violations.append(violation)
                else:
                    warnings.append(violation)

        return PolicyResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
            evaluated_rules=len(rules),
        )
