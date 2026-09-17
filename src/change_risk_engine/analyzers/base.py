"""``ChangeAnalyzer``: the interface every change-type analyzer implements.

Adding a new domain (application code, API definitions, Terraform,
Kubernetes YAML) is "implement this interface and register it," not "add
another branch to the risk engine" -- see docs/application-expansion.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from change_risk_engine.domain.change import Change, ChangeOperation
from change_risk_engine.domain.enums import ChangeType


class ChangeAnalyzer(ABC):
    """Turns a ``Change``'s raw content into structured ``ChangeOperation``s.

    An analyzer never scores risk and never touches dependencies or blast
    radius -- see the package docstring. Its only job is: given the raw
    text of a change, produce the structured, typed facts
    (``change_risk_engine.pipeline.assess`` runs dependency discovery,
    blast radius, risk scoring, and policy evaluation afterward, uniformly,
    regardless of which analyzer ran).
    """

    change_type: ChangeType

    @abstractmethod
    def supports(self, change: Change) -> bool:
        """Return True if this analyzer can handle ``change``."""

    @abstractmethod
    def analyze(self, change: Change) -> list[ChangeOperation]:
        """Parse ``change.raw_content`` into structured operations.

        Raises ``change_risk_engine.exceptions.ParseError`` if the content
        cannot be parsed at all. Individual unparsable statements should
        not abort the whole change -- see
        ``change_risk_engine.analyzers.database.parser`` for how partial
        parse failures surface as uncertainty instead.
        """


class ChangeAnalyzerRegistry:
    """Selects the right analyzer(s) for a change by type.

    A plain list + linear scan, not a plugin-discovery framework --
    section 29 of the product brief: favor simple implementations that
    leave clean extension points. Registering a new analyzer is one call
    to ``register``.
    """

    def __init__(self) -> None:
        self._analyzers: list[ChangeAnalyzer] = []

    def register(self, analyzer: ChangeAnalyzer) -> None:
        self._analyzers.append(analyzer)

    def resolve(self, change: Change) -> list[ChangeAnalyzer]:
        return [a for a in self._analyzers if a.supports(change)]
