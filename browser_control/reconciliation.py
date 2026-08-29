"""Evidence-based Chromium target reconciliation.

This module deliberately has no focus, visibility, recency, progress, or DOM
quality preference. Those observations are diagnostics, not authority proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .controller import TargetCandidate, TargetIdentity, TargetSelectionStatus


PROVEN_STALE = "PROVEN_STALE"
CANDIDATE = "candidate"
NON_CANDIDATE = "non_candidate"


@dataclass(frozen=True)
class TargetReconciliation:
    status: TargetSelectionStatus
    canonical: TargetCandidate | None
    candidates: tuple[TargetCandidate, ...]
    roles: tuple[tuple[str, str], ...]
    reasons: tuple[str, ...]

    @property
    def mutations_allowed(self) -> bool:
        return self.status is TargetSelectionStatus.TARGET_SELECTED

    @property
    def proven_stale(self) -> tuple[TargetCandidate, ...]:
        return tuple(candidate for candidate in self.candidates if self.role_for(candidate.target_id) == PROVEN_STALE)

    def role_for(self, target_id: str) -> str:
        return dict(self.roles).get(target_id, NON_CANDIDATE)

    def diagnostic(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "mutations_allowed": self.mutations_allowed,
            "canonical_target_id": self.canonical.target_id if self.canonical else None,
            "reasons": list(self.reasons),
            "candidates": [
                {
                    **candidate.diagnostic(),
                    "role": self.role_for(candidate.target_id),
                    "stale_reason": candidate.evidence.proven_stale_reason()
                    if candidate.evidence is not None and self.role_for(candidate.target_id) == PROVEN_STALE
                    else None,
                }
                for candidate in self.candidates
            ],
        }


class TargetReconciler:
    """Select only when one live candidate remains after explicit stale proof."""

    def reconcile(
        self,
        candidates: Iterable[TargetCandidate],
        identity: TargetIdentity | None = None,
    ) -> TargetReconciliation:
        eligible = tuple(
            candidate
            for candidate in candidates
            if candidate.matches
            and candidate.target_type == "page"
            and (identity is None or identity.matches(candidate))
        )
        roles: list[tuple[str, str]] = []
        live: list[TargetCandidate] = []
        stale: list[TargetCandidate] = []
        for candidate in eligible:
            stale_reason = candidate.evidence.proven_stale_reason() if candidate.evidence else None
            if stale_reason is None:
                roles.append((candidate.target_id, CANDIDATE))
                live.append(candidate)
            else:
                roles.append((candidate.target_id, PROVEN_STALE))
                stale.append(candidate)

        if not live:
            status = TargetSelectionStatus.NO_TARGET
            canonical = None
            reasons = ("no live page target is eligible",) if not stale else ("all eligible targets are PROVEN_STALE",)
        elif len(live) == 1:
            status = TargetSelectionStatus.TARGET_SELECTED
            canonical = live[0]
            reasons = (
                "exactly one live page target is eligible",
                *(f"{candidate.target_id} is PROVEN_STALE: {candidate.evidence.proven_stale_reason()}" for candidate in stale),
            )
        else:
            status = TargetSelectionStatus.AMBIGUOUS_TARGET
            canonical = None
            reasons = (
                f"{len(live)} live page targets remain; no browser lifecycle invariant proves authority",
                "progress, focus, visibility, recency, DOM fingerprint, and opener relationship are diagnostic only",
            )

        return TargetReconciliation(status, canonical, eligible, tuple(roles), tuple(reasons))

    @staticmethod
    def close_proven_stale_targets(
        reconciliation: TargetReconciliation,
        close_target: Callable[[str], bool],
    ) -> tuple[str, ...]:
        """Close only targets explicitly classified PROVEN_STALE.

        The caller must print the planned targets and reasons before invoking
        this method. The canonical target is never in the close set.
        """

        if reconciliation.canonical is None:
            raise ValueError("cannot close stale targets without a canonical target")
        closed: list[str] = []
        for candidate in reconciliation.proven_stale:
            if candidate.target_id == reconciliation.canonical.target_id:
                continue
            if close_target(candidate.target_id):
                closed.append(candidate.target_id)
        return tuple(closed)
