"""Programmatic overrides for a request document, applied before schema
validation.

Exists so a request template never has to be hand-edited or copied just
to change the handful of fields that vary per instance (name, sizing,
extensions, namespace). The template still supplies the fields with no
sensible default (``owner``, ``cost_center``, ``data_classification``) --
overrides customize the per-instance specifics on top of it, and go
through the exact same Pydantic validation a hand-written value would
(``_NAME_RE``, the K8s namespace pattern, etc.), because they're merged
into the raw document *before* ``DatabaseRequest.model_validate`` runs --
see ``dbre_platform.config.loader.load_database_request``.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

import yaml

from dbre_platform.exceptions import ConfigurationError


def set_field_path(document: dict[str, Any], path: str, value: Any) -> None:
    """Set a dotted path (e.g. ``spec.resources.cpu_request``) on a nested
    dict in place, creating intermediate mappings as needed."""
    parts = path.split(".")
    if not all(parts):
        raise ConfigurationError(f"Invalid field path {path!r} (empty segment).")
    current = document
    for part in parts[:-1]:
        next_value = current.setdefault(part, {})
        if not isinstance(next_value, dict):
            raise ConfigurationError(
                f"Cannot set {path!r}: {part!r} is not a mapping in the request document."
            )
        current = next_value
    current[parts[-1]] = value


def parse_set_value(raw: str) -> Any:
    """Parse a ``--set`` value the way a hand-written YAML scalar would:
    ``"20"`` -> ``20``, ``"true"`` -> ``True``, ``"250m"`` stays the string
    ``"250m"`` (not a recognized YAML scalar type) -- the same coercion a
    value written directly in the template gets, via the same parser
    (``yaml.safe_load``), so ``--set`` values and hand-written values are
    never treated differently downstream.
    """
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw
    return parsed if parsed is not None else raw


def apply_field_overrides(document: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``document`` with every ``{dotted.path: value}``
    pair in ``overrides`` applied."""
    if not overrides:
        return document
    result = copy.deepcopy(document)
    for path, value in overrides.items():
        set_field_path(result, path, value)
    return result


def apply_extension_additions(document: dict[str, Any], extensions: Sequence[str]) -> dict[str, Any]:
    """Return a copy of ``document`` with ``extensions`` appended to
    ``spec.extensions`` (deduplicated, order-preserving, additive --
    never removes an extension the template already lists)."""
    if not extensions:
        return document
    result = copy.deepcopy(document)
    spec = result.setdefault("spec", {})
    if not isinstance(spec, dict):
        raise ConfigurationError("Cannot add extensions: 'spec' is not a mapping in the request document.")
    existing = list(spec.get("extensions", []))
    for extension in extensions:
        if extension not in existing:
            existing.append(extension)
    spec["extensions"] = existing
    return result


# Named flag -> dotted schema path. Kept as one table so the CLI layer and
# any future caller (a REST API, a TUI) build overrides identically.
NAMED_FIELD_PATHS: dict[str, str] = {
    "name": "metadata.name",
    "namespace": "spec.namespace",
    "cpu_request": "spec.resources.cpu_request",
    "memory_request": "spec.resources.memory_request",
    "cpu_limit": "spec.resources.cpu_limit",
    "memory_limit": "spec.resources.memory_limit",
}


def build_field_overrides(named: dict[str, Any], set_values: Sequence[str] = ()) -> dict[str, Any]:
    """Build a flat ``{dotted.path: value}`` mapping from named flag values
    (``named``, keyed by the same names as ``NAMED_FIELD_PATHS``, ``None``
    meaning "not provided") plus raw ``--set FIELD=VALUE`` strings.

    ``--set`` is applied after the named flags, so ``--set metadata.name=x``
    can override ``--name`` if both are somehow given -- callers shouldn't
    rely on that ordering, but it's deterministic rather than an error.
    """
    overrides: dict[str, Any] = {}
    for flag_name, value in named.items():
        if value is None:
            continue
        try:
            path = NAMED_FIELD_PATHS[flag_name]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown override flag {flag_name!r}.") from exc
        overrides[path] = value

    for item in set_values:
        if "=" not in item:
            raise ConfigurationError(f"--set value must be FIELD=VALUE, got {item!r}.")
        path, _, raw_value = item.partition("=")
        path = path.strip()
        if not path:
            raise ConfigurationError(f"--set value must be FIELD=VALUE, got {item!r}.")
        overrides[path] = parse_set_value(raw_value)

    return overrides
