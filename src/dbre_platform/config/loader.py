"""Load and validate a database request YAML file."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.exceptions import ConfigurationError


def load_database_request(path: str | Path) -> DatabaseRequest:
    """Parse and validate a database request file.

    Raises ``ConfigurationError`` (never a raw ``yaml`` or ``pydantic``
    exception) so every caller -- CLI, tests, future API -- gets one
    predictable error type to handle.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigurationError(f"Request file not found: {file_path}")

    try:
        raw = yaml.safe_load(file_path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {file_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigurationError(f"{file_path} must contain a YAML mapping at the top level.")

    try:
        return DatabaseRequest.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(_format_validation_error(file_path, exc)) from exc


def _format_validation_error(file_path: Path, exc: ValidationError) -> str:
    lines = [f"{file_path} failed schema validation:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)
