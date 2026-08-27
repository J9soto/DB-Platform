"""Tag resolution: compute the final tag set applied to a provisioned resource.

Precedence (lowest to highest, so required tags can never be shadowed):

1. Developer-supplied custom tags (``spec.tags`` in the request)
2. Platform-wide tags from policy (``global_tags`` in policies/tagging.yaml)
3. The six required tags computed from request metadata
   (``DatabaseRequest.rendered_tags()``)

This is what "tags should be configurable through policy" means in
practice: a platform team can add or change a platform-wide tag (say, a new
`cost_allocation_v2` key) by editing YAML, with no code change and no
redeploy of the CLI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dbre_platform.config.models import DatabaseRequest

DEFAULT_TAGGING_POLICY = Path(__file__).resolve().parents[3] / "policies" / "tagging.yaml"


def load_global_tags(policy_path: str | Path = DEFAULT_TAGGING_POLICY) -> dict[str, str]:
    path = Path(policy_path)
    if not path.exists():
        return {}
    document: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    return dict(document.get("global_tags", {}))


def resolve_tags(
    request: DatabaseRequest, policy_path: str | Path = DEFAULT_TAGGING_POLICY
) -> dict[str, str]:
    """Compute the final, ordered tag set for a request."""
    tags: dict[str, str] = dict(request.spec.tags)
    tags.update(load_global_tags(policy_path))
    tags.update(request.rendered_tags())
    return tags
