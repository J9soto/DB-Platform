"""``Explainer`` implementations. See the package docstring for the
"augmentation, never the source of truth" rule this module exists to obey.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from change_risk_engine.domain.change import Change
from change_risk_engine.domain.risk import ChangeRiskAssessment


class Explainer(ABC):
    @abstractmethod
    def explain(self, change: Change, assessment: ChangeRiskAssessment) -> str:
        """Return a plain-English narrative of ``assessment``.

        Must not alter ``assessment.overall_score``, ``risk_level``, or
        any factor -- an explainer narrates, it never re-decides.
        """


class RuleBasedExplainer(Explainer):
    """Deterministic, template-based narration. The default explainer.

    No network call, no model, no vendor dependency -- every word traces
    to a field already on ``assessment``. This is what "explainable" means
    concretely: run this twice on the same assessment and get the same
    paragraph both times.
    """

    def explain(self, change: Change, assessment: ChangeRiskAssessment) -> str:
        sentences: list[str] = []

        sentences.append(
            f"This change is assessed as {assessment.risk_level.value.upper()} risk "
            f"(score {assessment.overall_score:.0f}/100, {assessment.confidence * 100:.0f}% confidence)."
        )

        top = assessment.top_factors(3)
        if top:
            reasons = "; ".join(f.reason.rstrip(".") for f in top)
            sentences.append(f"The largest contributors are: {reasons}.")

        br = assessment.blast_radius
        if br is not None:
            pieces = []
            if br.affected_services:
                pieces.append(f"{len(br.affected_services)} service(s)")
            if br.affected_apis:
                pieces.append(f"{len(br.affected_apis)} API(s)")
            if br.affected_data_pipelines:
                pieces.append(f"{len(br.affected_data_pipelines)} data pipeline(s)")
            if br.affected_tables:
                pieces.append(f"{len(br.affected_tables)} table/view(s)")
            if pieces:
                sentences.append(
                    f"Blast radius is estimated as {br.estimated_scope}, touching {', '.join(pieces)}."
                )
            if br.critical_dependencies:
                sentences.append(f"Critical dependencies involved: {', '.join(br.critical_dependencies)}.")

        if assessment.requires_approval:
            sentences.append("Policy requires explicit approval before this change can be deployed.")

        if assessment.recommendations:
            required = [r.detail for r in assessment.recommendations if r.priority.value == "required"]
            if required:
                sentences.append("Required before deploying: " + " ".join(required))

        if assessment.uncertainty:
            gaps = "; ".join(u.description for u in assessment.uncertainty[:3])
            sentences.append(f"Known gaps in this assessment: {gaps}.")

        return " ".join(sentences)


def build_grounded_context(change: Change, assessment: ChangeRiskAssessment) -> str:
    """Build a plain-text summary of an assessment's real evidence, suitable
    as the only factual input to an LLM prompt -- see ``LLMExplainer``.

    Deliberately excludes nothing and invents nothing: every line here is
    a field already on ``assessment``.
    """
    lines = [
        f"Change: {change.title} ({change.change_type.value}, environment={change.environment})",
        f"Overall risk score: {assessment.overall_score:.1f}/100 ({assessment.risk_level.value}), "
        f"confidence {assessment.confidence:.2f}",
        "Risk factors:",
    ]
    for f in sorted(assessment.factors, key=lambda f: f.contribution, reverse=True):
        lines.append(
            f"  - {f.factor_type.value} = {f.label} (score {f.score}, weight {f.weight}): {f.reason}"
        )
    if assessment.blast_radius:
        lines.append(f"Blast radius: {assessment.blast_radius.to_dict()}")
    if assessment.uncertainty:
        lines.append("Uncertainty:")
        for u in assessment.uncertainty:
            lines.append(f"  - {u.description} ({u.reason})")
    return "\n".join(lines)


class LLMExplainer(Explainer):
    """Narrates an assessment using a caller-supplied completion function.

    ``complete`` takes a prompt string and returns model text -- deliberately
    not tied to any one vendor's SDK (Anthropic, OpenAI, etc.); the caller
    wires that up. The prompt is built entirely from
    ``build_grounded_context`` so the model is narrating real evidence, not
    inventing a risk judgment -- if ``complete`` raises or returns empty
    text, callers should fall back to ``RuleBasedExplainer`` rather than
    surface nothing (see ``change_risk_engine.pipeline``).
    """

    def __init__(self, complete: Callable[[str], str]) -> None:
        self._complete = complete

    def explain(self, change: Change, assessment: ChangeRiskAssessment) -> str:
        context = build_grounded_context(change, assessment)
        prompt = (
            "You are explaining a database change risk assessment to an engineer. "
            "Use ONLY the facts below -- do not invent additional risks, evidence, or "
            "numbers. Write 3-5 plain sentences a busy on-call engineer can read in "
            "ten seconds.\n\n" + context
        )
        return self._complete(prompt)
