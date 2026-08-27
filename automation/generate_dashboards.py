#!/usr/bin/env python3
"""Regenerate the vendor dashboard JSON files under monitoring/dashboards/
from the single vendor-neutral dashboard model.

Run this whenever PLATFORM_OVERVIEW_DASHBOARD changes -- the committed JSON
files are generated output, not hand-maintained, so they never drift from
the model. See dbre_platform.observability.dashboards.
"""

from __future__ import annotations

import json
from pathlib import Path

from dbre_platform.observability import (
    PLATFORM_OVERVIEW_DASHBOARD,
    to_cloudwatch_dashboard_json,
    to_dynatrace_tiles_json,
    to_grafana_dashboard_json,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "monitoring" / "dashboards"


def main() -> None:
    targets = {
        OUT_DIR / "grafana" / "platform-overview.grafana.json": to_grafana_dashboard_json(
            PLATFORM_OVERVIEW_DASHBOARD
        ),
        OUT_DIR / "cloudwatch" / "platform-overview.cloudwatch.json": to_cloudwatch_dashboard_json(
            PLATFORM_OVERVIEW_DASHBOARD
        ),
        OUT_DIR / "dynatrace" / "platform-overview.dynatrace.json": to_dynatrace_tiles_json(
            PLATFORM_OVERVIEW_DASHBOARD
        ),
    }
    for path, document in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2) + "\n")
        print(f"wrote {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
