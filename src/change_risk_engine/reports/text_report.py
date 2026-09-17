"""Renders a ``ChangeRiskAssessment`` as the human-readable report from
section 11 of the product brief -- what an engineer reads in the CLI or
pastes into a PR comment.
"""

from __future__ import annotations

from change_risk_engine.domain.change import Change
from change_risk_engine.domain.dependency import is_replication_path
from change_risk_engine.domain.risk import ChangeRiskAssessment

_RULE = "-" * 60


def _section(title: str) -> list[str]:
    return [_RULE, title, _RULE, ""]


def render_text_report(change: Change, assessment: ChangeRiskAssessment) -> str:
    lines: list[str] = []

    lines += _section("CHANGE RISK ASSESSMENT")
    lines += ["Change:", change.title or change.raw_content.strip().splitlines()[0][:200], ""]
    lines += ["Overall Risk:", assessment.risk_level.value.upper(), ""]
    lines += ["Risk Score:", f"{assessment.overall_score:.0f} / 100", ""]
    lines += ["Confidence:", f"{assessment.confidence * 100:.0f}%", ""]
    lines += ["Production:", "YES" if change.is_production() else "NO", ""]
    if assessment.requires_approval:
        lines += ["Approval required:", "YES -- see POLICY DECISIONS below", ""]

    lines += _section("WHY?")
    top = assessment.top_factors(len(assessment.factors))
    if top:
        for i, factor in enumerate(top, start=1):
            possible = factor.weight * 100
            lines.append(
                f"{i}. [{factor.label}] {factor.reason} "
                f"(contributes {factor.contribution:.1f} of {possible:.0f} possible points)"
            )
    else:
        lines.append("No risk factors were computed for this change.")
    lines.append("")

    lines += _section("BLAST RADIUS")
    br = assessment.blast_radius
    if br is None:
        lines.append("No dependency graph was available -- blast radius could not be computed.")
    else:
        direct_names = [r.resource.name for r in br.direct_impact]
        lines.append("Direct:")
        lines.append("  " + (", ".join(direct_names) if direct_names else "(none)"))
        lines.append("")
        replication_count = sum(1 for r in br.downstream_impact if is_replication_path(r))
        lines.append(f"Applications/services: {len(br.affected_services)}")
        lines.append(f"Critical services: {len(br.critical_dependencies)}")
        lines.append(f"APIs: {len(br.affected_apis)}")
        lines.append(f"Data pipelines: {len(br.affected_data_pipelines)}")
        lines.append(f"Replication targets: {replication_count}")
        lines.append(f"Estimated scope: {br.estimated_scope} (confidence {br.confidence:.2f})")
    lines.append("")

    lines += _section("RECOMMENDATIONS")
    if assessment.recommendations:
        for rec in sorted(
            assessment.recommendations,
            key=lambda r: {"required": 0, "recommended": 1, "optional": 2}[r.priority.value],
        ):
            marker = {
                "required": "✓ [REQUIRED]",
                "recommended": "✓ [RECOMMENDED]",
                "optional": "○ [OPTIONAL]",
            }[rec.priority.value]
            lines.append(f"{marker} {rec.detail}")
    else:
        lines.append("(none)")
    lines.append("")

    lines += _section("POLICY DECISIONS")
    triggered = [d for d in assessment.policy_decisions if d.triggered]
    if triggered:
        for d in triggered:
            lines.append(f"[{d.severity.upper()}] {d.policy_id} v{d.version}: {d.description}")
    else:
        lines.append("No policy rules triggered.")
    lines.append("")

    lines += _section("EVIDENCE")
    if assessment.evidence:
        for e in assessment.evidence:
            lines.append(f"- ({e.source}) {e.description}")
    else:
        lines.append("(none)")
    lines.append("")

    lines += _section("UNCERTAINTY")
    if assessment.uncertainty:
        for u in assessment.uncertainty:
            lines.append(f"- {u.description} -- {u.reason}")
    else:
        lines.append("No known gaps in this assessment.")
    lines.append(_RULE)

    if assessment.explanation:
        lines += ["", "AI EXPLANATION", assessment.explanation]

    return "\n".join(lines)
