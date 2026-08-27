"""Field resolution and comparison operators used by policy rules.

A rule is data (see policies/environments/*.yaml), not code -- adding a new
guardrail should never require a code change. This module is the small,
fixed set of primitives that data is allowed to invoke.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from dbre_platform.exceptions import ConfigurationError


def resolve_field_path(document: dict[str, Any], path: str) -> Any:
    """Resolve a dotted path (e.g. ``spec.slo.availability_target``) against
    a nested dict, as produced by ``DatabaseRequest.model_dump()``.

    Returns ``None`` if any segment of the path is missing, so rules that
    check for the *absence* of an optional field behave sensibly rather than
    raising.
    """
    current: Any = document
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return None
    return current


def _op_equals(actual: Any, expected: Any) -> bool:
    return actual == expected


def _op_not_equals(actual: Any, expected: Any) -> bool:
    return actual != expected


def _op_gte(actual: Any, expected: Any) -> bool:
    return actual is not None and actual >= expected


def _op_lte(actual: Any, expected: Any) -> bool:
    return actual is not None and actual <= expected


def _op_gt(actual: Any, expected: Any) -> bool:
    return actual is not None and actual > expected


def _op_lt(actual: Any, expected: Any) -> bool:
    return actual is not None and actual < expected


def _op_min_length(actual: Any, expected: Any) -> bool:
    return actual is not None and len(actual) >= expected


def _op_max_length(actual: Any, expected: Any) -> bool:
    return actual is not None and len(actual) <= expected


def _op_in(actual: Any, expected: Any) -> bool:
    return actual in expected


def _op_not_in(actual: Any, expected: Any) -> bool:
    return actual not in expected


def _op_contains(actual: Any, expected: Any) -> bool:
    return actual is not None and expected in actual


def _op_regex(actual: Any, expected: Any) -> bool:
    return actual is not None and re.match(expected, str(actual)) is not None


OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "equals": _op_equals,
    "not_equals": _op_not_equals,
    "gte": _op_gte,
    "lte": _op_lte,
    "gt": _op_gt,
    "lt": _op_lt,
    "min_length": _op_min_length,
    "max_length": _op_max_length,
    "in": _op_in,
    "not_in": _op_not_in,
    "contains": _op_contains,
    "regex": _op_regex,
}


def evaluate_operator(operator: str, actual: Any, expected: Any) -> bool:
    try:
        fn = OPERATORS[operator]
    except KeyError as exc:
        raise ConfigurationError(
            f"Unknown policy operator {operator!r}. Supported: {sorted(OPERATORS)}"
        ) from exc
    return fn(actual, expected)
