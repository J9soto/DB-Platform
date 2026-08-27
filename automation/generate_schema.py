#!/usr/bin/env python3
"""Regenerate schemas/database-request.schema.json from the Pydantic models.

The schema is generated output, not hand-maintained -- it can never drift
from dbre_platform.config.models because it's produced directly from it.
Run this whenever the request schema changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from dbre_platform.config.models import DatabaseRequest

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "schemas" / "database-request.schema.json"


def main() -> None:
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        **DatabaseRequest.model_json_schema(by_alias=True),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
