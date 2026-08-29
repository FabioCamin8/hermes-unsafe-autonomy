"""Small, dependency-free policy layer for deterministic zyBooks control.

The module deliberately does not know how to answer or submit coursework.  A
browser adapter supplies sanitized observations and, only after the policy
checks pass, receives an activity-local scope for an explicitly authorized
operation.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import urlsplit


class TargetSelectionStatus(str, Enum):
    NO_TARGET = "NO_TARGET"
    TARGET_SELECTED = "TARGET_SELECTED"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"


class ActivityKind(str, Enum):
    KNOWN_SAFE_ACTIVITY = "KNOWN_SAFE_ACTIVITY"
    PROTECTED_SORTABLE_MATCHING = "PROTECTED_SORTABLE_MATCHING"
    PROTECTED_DRAG_AND_DROP = "PROTECTED_DRAG_AND_DROP"
    PROTECTED_CHALLENGE = "PROTECTED_CHALLENGE"
    PROTECTED_LAB = "PROTECTED_LAB"
    UNKNOWN = "UNKNOWN"


class RetryState(str, Enum):
    INSPECT = "INSPECT"
    CLASSIFY = "CLASSIFY"
    READY = "READY"
    ATTEMPT_1 = "ATTEMPT_1"
    VERIFY = "VERIFY"
    DIAGNOSE = "DIAGNOSE"
    ATTEMPT_2 = "ATTEMPT_2"
    RECORD_AND_STOP = "RECORD_AND_STOP"
    BLOCKED = "BLOCKED"


class ResourceState(str, Enum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    TERMINAL = "TERMINAL"
    EXPIRED = "EXPIRED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_path(url: str) -> str:
    """Return only a URL path; authenticated query material never persists."""

    return urlsplit(url).path or "/"


def _compact_error(error: str | None) -> str | None:
    if error is None:
        return None
    raw = str(error)
    category = "browser_error"
    for marker, name in (
        ("timeout", "timeout"),
        ("stale", "stale"),
        ("target", "target"),
        ("focus", "focus"),
        ("disabled", "disabled"),
        ("unchanged", "unchanged"),
        ("permission", "permission"),
        ("display", "display"),
        ("x11", "x11"),
        ("screenshot", "screenshot"),
        ("generation", "generation"),
        ("activity", "activity"),
    ):
        if marker in raw.lower():
            category = name
            break
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]
    return f"{category}:{digest}"


@dataclass(frozen=True)
class TargetEvidence:
    """Browser/CDP evidence used for reconciliation, never for heuristics."""

    target_type: str = "page"
    browser_context_id: str | None = None
    opener_target_id: str | None = None
    opener_frame_id: str | None = None
    parent_frame_id: str | None = None
    attached: bool | None = None
    window_id: int | None = None
    window_state: str | None = None
    target_present: bool = True
    cdp_reachable: bool | None = None
    lifecycle_state: str | None = None
    navigation_entry_count: int | None = None
    current_navigation_entry_id: int | None = None
    frame_id: str | None = None
    loader_id: str | None = None
    ready_state: str | None = None
    visibility_state: str | None = None
    has_focus: bool | None = None
    prerendering: bool | None = None
    was_discarded: bool | None = None
    performance_time_origin_ms: int | None = None
    performance_now_ms: int | None = None
    section_heading: str | None = None
    activity_count: int | None = None
    document_generation: str | None = None
    completion_percentage: float | None = None

    def proven_stale_reason(self) -> str | None:
        """Return a reason only when CDP lifecycle evidence proves staleness."""

        if not self.target_present:
            return "target absent from the current Target.getTargets snapshot"
        if self.cdp_reachable is False:
            return "target CDP page session is disconnected"
        if self.lifecycle_state == "detached":
            return "target lifecycle is explicitly detached"
        return None

    def diagnostic(self) -> dict[str, Any]:
        return {
            "target_type": self.target_type,
            "browser_context_id": self.browser_context_id,
            "opener_target_id": self.opener_target_id,
            "opener_frame_id": self.opener_frame_id,
            "parent_frame_id": self.parent_frame_id,
            "attached": self.attached,
            "window_id": self.window_id,
            "window_state": self.window_state,
            "target_present": self.target_present,
            "cdp_reachable": self.cdp_reachable,
            "lifecycle_state": self.lifecycle_state,
            "navigation_entry_count": self.navigation_entry_count,
            "current_navigation_entry_id": self.current_navigation_entry_id,
            "frame_id": self.frame_id,
            "loader_id": self.loader_id,
            "ready_state": self.ready_state,
            "visibility_state": self.visibility_state,
            "has_focus": self.has_focus,
            "prerendering": self.prerendering,
            "was_discarded": self.was_discarded,
            "performance_time_origin_ms": self.performance_time_origin_ms,
            "performance_now_ms": self.performance_now_ms,
            "section_heading": self.section_heading,
            "activity_count": self.activity_count,
            "document_generation": self.document_generation,
            "completion_percentage": self.completion_percentage,
        }


@dataclass(frozen=True)
class TargetCandidate:
    target_id: str
    url: str
    course: str | None = None
    chapter: str | None = None
    section: str | None = None
    title: str | None = None
    dom_section_heading: str | None = None
    page_fingerprint: str = ""
    matches: bool = True
    target_type: str = "page"
    evidence: TargetEvidence | None = None

    def diagnostic(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "path": _safe_path(self.url),
            "course": self.course,
            "chapter": self.chapter,
            "section": self.section,
            "title": self.title,
            "dom_section_heading": self.dom_section_heading,
            "page_fingerprint": self.page_fingerprint,
            "target_type": self.target_type,
            "evidence": self.evidence.diagnostic() if self.evidence is not None else None,
        }


@dataclass(frozen=True)
class TargetIdentity:
    course: str | None = None
    chapter: str | None = None
    section: str | None = None
    title: str | None = None
    dom_section_heading: str | None = None
    page_fingerprint: str | None = None

    def matches(self, candidate: TargetCandidate) -> bool:
        comparisons = (
            (self.course, candidate.course),
            (self.chapter, candidate.chapter),
            (self.section, candidate.section),
            (self.title, candidate.title),
            (self.dom_section_heading, candidate.dom_section_heading),
            (self.page_fingerprint, candidate.page_fingerprint),
        )
        return all(expected is None or expected == observed for expected, observed in comparisons)


@dataclass(frozen=True)
class TargetSelection:
    status: TargetSelectionStatus
    candidate: TargetCandidate | None
    candidates: tuple[TargetCandidate, ...]

    @property
    def mutations_allowed(self) -> bool:
        return self.status is TargetSelectionStatus.TARGET_SELECTED

    def diagnostic(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "mutations_allowed": self.mutations_allowed,
            "candidates": [candidate.diagnostic() for candidate in self.candidates],
        }


class TargetSelector:
    def __init__(self, telemetry: "Telemetry | None" = None) -> None:
        self.telemetry = telemetry

    def select(
        self,
        candidates: Iterable[TargetCandidate],
        identity: TargetIdentity | None = None,
    ) -> TargetSelection:
        eligible = tuple(candidate for candidate in candidates if candidate.matches and (identity is None or identity.matches(candidate)))
        if len(eligible) == 0:
            result = TargetSelection(TargetSelectionStatus.NO_TARGET, None, ())
        elif len(eligible) == 1:
            result = TargetSelection(TargetSelectionStatus.TARGET_SELECTED, eligible[0], eligible)
        else:
            result = TargetSelection(TargetSelectionStatus.AMBIGUOUS_TARGET, None, eligible)
        if self.telemetry:
            self.telemetry.emit(
                "TARGET",
                status=result.status.value,
                candidate_count=len(eligible),
                target_ids=[candidate.target_id for candidate in eligible],
            )
        return result


@dataclass(frozen=True)
class ActivityRecord:
    activity_index: int
    activity_id: str
    section: str
    participation_marker: bool = False
    challenge_markers: tuple[str, ...] = ()
    lab_markers: tuple[str, ...] = ()
    major_control_types: tuple[str, ...] = ()
    aria_roles: tuple[str, ...] = ()
    iframe_count: int = 0
    native_draggable_count: int = 0
    sortable_markers: tuple[str, ...] = ()
    term_bank_markers: tuple[str, ...] = ()
    sortable_container_count: int = 0
    sortable_item_count: int = 0
    term_bank_container_count: int = 0
    focusable_custom_item_count: int = 0
    non_native_draggable_count: int = 0
    drag_drop_roles: tuple[str, ...] = ()
    pointer_markers: tuple[str, ...] = ()
    interaction_aria_markers: tuple[str, ...] = ()
    canvas_count: int = 0
    svg_count: int = 0
    completion_markers: tuple[str, ...] = ()
    check_control_count: int = 0
    enabled_check_control_count: int = 0
    submit_control_count: int = 0
    enabled_submit_control_count: int = 0
    visible: bool = False
    fingerprint: str = ""
    kind: ActivityKind = ActivityKind.UNKNOWN
    keyboard_reorder_markers: tuple[str, ...] = ()

    def with_kind(self, kind: ActivityKind) -> "ActivityRecord":
        return replace(self, kind=kind)

    def custom_interaction_signals(self) -> tuple[str, ...]:
        signals: list[str] = []
        if self.sortable_container_count:
            signals.append("sortable_container")
        if self.sortable_item_count:
            signals.append("sortable_item")
        if self.term_bank_container_count:
            signals.append("term_bank")
        if self.focusable_custom_item_count:
            signals.append("focusable_custom_item")
        if self.non_native_draggable_count and (
            self.sortable_container_count
            or self.sortable_item_count
            or self.term_bank_container_count
            or self.drag_drop_roles
        ):
            signals.append("non_native_draggable")
        if self.native_draggable_count:
            signals.append("native_draggable")
        if self.sortable_markers:
            signals.extend(f"sortable:{marker}" for marker in self.sortable_markers)
        if self.term_bank_markers:
            signals.extend(f"term_bank:{marker}" for marker in self.term_bank_markers)
        if self.drag_drop_roles:
            signals.extend(f"role:{role}" for role in self.drag_drop_roles)
        if self.pointer_markers:
            signals.extend(f"pointer:{marker}" for marker in self.pointer_markers)
        if self.interaction_aria_markers:
            signals.extend(f"aria:{marker}" for marker in self.interaction_aria_markers)
        if self.keyboard_reorder_markers:
            signals.extend(f"keyboard:{marker}" for marker in self.keyboard_reorder_markers)
        if self.canvas_count:
            signals.append("canvas")
        if self.svg_count and (self.pointer_markers or self.keyboard_reorder_markers):
            signals.append("svg_gesture")
        return tuple(dict.fromkeys(signals))

    @property
    def custom_interaction_candidate(self) -> bool:
        return bool(self.custom_interaction_signals())

    @property
    def sortable_matching_candidate(self) -> bool:
        """Require multiple independent structure signals before subtyping."""

        has_option_role = any(role.lower() == "option" for role in self.drag_drop_roles)
        has_sortable_items = self.sortable_item_count > 0
        return has_sortable_items and (
            self.term_bank_container_count > 0
            or (
                self.sortable_container_count > 0
                and (has_option_role or self.focusable_custom_item_count > 0)
            )
        )

    def diagnostic(self) -> dict[str, Any]:
        return {
            "activity_index": self.activity_index,
            "activity_id": self.activity_id,
            "section": self.section,
            "kind": self.kind.value,
            "participation_marker": self.participation_marker,
            "major_control_types": list(self.major_control_types),
            "aria_roles": list(self.aria_roles),
            "iframes": self.iframe_count,
            "native_draggable": self.native_draggable_count,
            "sortable_markers": list(self.sortable_markers),
            "term_bank_markers": list(self.term_bank_markers),
            "sortable_containers": self.sortable_container_count,
            "sortable_items": self.sortable_item_count,
            "term_bank_containers": self.term_bank_container_count,
            "focusable_custom_items": self.focusable_custom_item_count,
            "non_native_draggable": self.non_native_draggable_count,
            "drag_drop_roles": list(self.drag_drop_roles),
            "pointer_markers": list(self.pointer_markers),
            "interaction_aria_markers": list(self.interaction_aria_markers),
            "keyboard_reorder_markers": list(self.keyboard_reorder_markers),
            "canvas": self.canvas_count,
            "svg": self.svg_count,
            "completion_markers": list(self.completion_markers),
            "check_controls": {
                "count": self.check_control_count,
                "enabled": self.enabled_check_control_count,
            },
            "submit_controls": {
                "count": self.submit_control_count,
                "enabled": self.enabled_submit_control_count,
            },
            "visible": self.visible,
            "fingerprint": self.fingerprint,
            "custom_interaction_candidate": self.custom_interaction_candidate,
            "custom_interaction_signals": list(self.custom_interaction_signals()),
            "sortable_matching_candidate": self.sortable_matching_candidate,
            "keyboard_contract": "observed" if self.keyboard_reorder_markers else "unknown",
            # An inventory is never an authorization decision. The registry
            # must separately prove a safe activity and selected target.
            "mutation_allowed": False,
        }


class ActivityClassifier:
    """Classify with protected/unknown precedence; no generic fallback exists."""

    _SAFE_CONTROLS = frozenset(
        {
            "radio",
            "checkbox",
            "animation_button",
            "play_button",
            "render_button",
            "check_button",
            "button",
            "svg",
        }
    )

    def classify(self, record: ActivityRecord) -> ActivityKind:
        if record.challenge_markers:
            return ActivityKind.PROTECTED_CHALLENGE
        if record.lab_markers:
            return ActivityKind.PROTECTED_LAB
        if record.sortable_matching_candidate:
            return ActivityKind.PROTECTED_SORTABLE_MATCHING
        if (
            record.native_draggable_count
            or record.sortable_markers
            or record.term_bank_markers
            or record.sortable_container_count
            or record.sortable_item_count
            or record.drag_drop_roles
            or record.pointer_markers
            or record.interaction_aria_markers
            or record.canvas_count
            or record.keyboard_reorder_markers
        ):
            return ActivityKind.PROTECTED_DRAG_AND_DROP
        if record.iframe_count or not record.participation_marker:
            return ActivityKind.UNKNOWN
        controls = set(record.major_control_types)
        primary = {"radio", "checkbox", "animation_button", "play_button"}
        if not controls or not controls.issubset(self._SAFE_CONTROLS) or not controls.intersection(primary):
            return ActivityKind.UNKNOWN
        return ActivityKind.KNOWN_SAFE_ACTIVITY

    def inventory(self, records: Iterable[ActivityRecord]) -> tuple[ActivityRecord, ...]:
        return tuple(record.with_kind(self.classify(record)) for record in records)

    def custom_interaction_candidates(self, records: Iterable[ActivityRecord]) -> tuple[dict[str, Any], ...]:
        """Return read-only candidates for a later custom-widget investigation."""

        candidates: list[dict[str, Any]] = []
        for activity in records:
            signals = activity.custom_interaction_signals()
            if signals:
                classified = activity.with_kind(self.classify(activity))
                candidates.append(
                    {
                        "activity_id": activity.activity_id,
                        "classification": classified.kind.value,
                        "signals": list(signals),
                        "native_draggable": activity.native_draggable_count,
                        "keyboard_contract": "observed" if activity.keyboard_reorder_markers else "unknown",
                        "mutation_allowed": False,
                    }
                )
        return tuple(candidates)


class Telemetry:
    """In-memory structured events with a deliberately narrow field surface."""

    _FORBIDDEN_KEYS = re.compile(r"answer|cookie|token|authorization|password|secret|credential|private.?key|html|inner.?text|input.?value", re.I)

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: str, **fields: Any) -> None:
        if any(self._FORBIDDEN_KEYS.search(key) for key in fields):
            raise ValueError("telemetry field is outside the secret-free schema")
        self.events.append({"event": event, "timestamp": _utc_now(), **self._safe_fields(fields)})

    @staticmethod
    def _safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
        def safe(value: Any) -> Any:
            if value is None or isinstance(value, (bool, int, float)):
                return value
            if isinstance(value, str):
                return value[:160]
            if isinstance(value, (list, tuple)):
                return [safe(item) for item in value[:32]]
            raise ValueError("telemetry value is outside the secret-free schema")

        return {key: safe(value) for key, value in fields.items()}


@dataclass(frozen=True)
class PageGenerationEvent:
    generation: int
    changed: bool
    reason: str


class PageGenerationTracker:
    def __init__(self, telemetry: Telemetry | None = None) -> None:
        self.generation = 0
        self.target_id: str | None = None
        self.page_fingerprint: str | None = None
        self.telemetry = telemetry

    def observe(self, target_id: str, page_fingerprint: str) -> PageGenerationEvent:
        changed = self.generation == 0 or target_id != self.target_id or page_fingerprint != self.page_fingerprint
        if changed:
            self.generation += 1
            self.target_id = target_id
            self.page_fingerprint = page_fingerprint
            reason = "initial_observation" if self.generation == 1 else "target_or_page_fingerprint_changed"
            if self.telemetry:
                self.telemetry.emit("GENERATION_CHANGE", generation=self.generation, target_id=target_id, reason=reason)
        else:
            reason = "unchanged"
        return PageGenerationEvent(self.generation, changed, reason)

    def invalidate(self, reason: str) -> PageGenerationEvent:
        self.generation += 1
        self.page_fingerprint = None
        if self.telemetry:
            self.telemetry.emit("GENERATION_CHANGE", generation=self.generation, target_id=self.target_id, reason=reason)
        return PageGenerationEvent(self.generation, True, reason)

    def accepts(self, target_id: str, generation: int) -> bool:
        return target_id == self.target_id and generation == self.generation


@dataclass(frozen=True)
class MutationRequest:
    target_id: str
    page_generation: int
    activity_id: str
    activity_root_fingerprint: str
    operation: str
    control_selector: str | None = None


@dataclass(frozen=True)
class MutationDecision:
    code: str
    allowed: bool
    reason: str
    activity: ActivityRecord | None = None


class RootLike(Protocol):
    def query_selector(self, selector: str) -> Any: ...

    def query_selector_all(self, selector: str) -> Iterable[Any]: ...


@dataclass(frozen=True)
class ActivityRoot:
    """Adapter result whose identity is checked before any operation."""

    activity_id: str
    fingerprint: str
    node: RootLike


class ActivityScope:
    """Only exposes selectors relative to one already-authorized root."""

    _GLOBAL_SELECTOR = re.compile(r"(^|[^\w-])(document|window|globalThis|:root|html|body)([^\w-]|$)", re.I)

    def __init__(self, root: RootLike, activity: ActivityRecord) -> None:
        self._root = root
        self.activity = activity

    @classmethod
    def _validate_selector(cls, selector: str) -> None:
        if not selector or cls._GLOBAL_SELECTOR.search(selector):
            raise ValueError("DOM selector must be activity-relative")

    def query_selector(self, selector: str) -> Any:
        self._validate_selector(selector)
        return self._root.query_selector(selector)

    def query_selector_all(self, selector: str) -> tuple[Any, ...]:
        self._validate_selector(selector)
        return tuple(self._root.query_selector_all(selector))


class ProtectedContainerRegistry:
    def __init__(self, selection: TargetSelection, telemetry: Telemetry | None = None) -> None:
        self.selection = selection
        self.telemetry = telemetry
        self.target_id: str | None = None
        self.page_generation: int | None = None
        self._activities: dict[str, ActivityRecord] = {}

    def bind(self, target_id: str, page_generation: int, activities: Iterable[ActivityRecord]) -> None:
        self.target_id = target_id
        self.page_generation = page_generation
        classified = ActivityClassifier().inventory(activities)
        if len({activity.activity_id for activity in classified}) != len(classified):
            raise ValueError("duplicate activity IDs cannot be registered safely")
        self._activities = {activity.activity_id: activity for activity in classified}
        if self.telemetry:
            self.telemetry.emit("INVENTORY", target_id=target_id, page_generation=page_generation, activity_count=len(self._activities))
            for activity in self._activities.values():
                self.telemetry.emit("CLASSIFICATION", activity_id=activity.activity_id, type=activity.kind.value, protected=activity.kind is not ActivityKind.KNOWN_SAFE_ACTIVITY)
                if activity.kind is not ActivityKind.KNOWN_SAFE_ACTIVITY:
                    self.telemetry.emit("PROTECTED", activity_id=activity.activity_id, reason=activity.kind.value)

    def authorize(self, request: MutationRequest) -> MutationDecision:
        if not self.selection.mutations_allowed:
            return MutationDecision(self.selection.status.value, False, "target selection is not unique")
        if request.target_id != self.target_id or request.target_id != self.selection.candidate.target_id:
            return MutationDecision("TARGET_MISMATCH", False, "request target is not the selected target")
        if request.page_generation != self.page_generation:
            return MutationDecision("STALE_PAGE_GENERATION", False, "request references an older page generation")
        activity = self._activities.get(request.activity_id)
        if activity is None:
            return MutationDecision("ACTIVITY_NOT_FOUND", False, "activity is not in the current inventory")
        if request.activity_root_fingerprint != activity.fingerprint:
            return MutationDecision("STALE_ACTIVITY_ROOT", False, "activity root fingerprint changed")
        if activity.kind is ActivityKind.UNKNOWN:
            return MutationDecision("UNKNOWN_ACTIVITY", False, "unknown activity is fail-closed", activity)
        if activity.kind is not ActivityKind.KNOWN_SAFE_ACTIVITY:
            return MutationDecision("PROTECTED_ACTIVITY", False, activity.kind.value, activity)
        return MutationDecision("AUTHORIZED_SCOPE", True, "activity-scoped operation may proceed", activity)

    def perform(
        self,
        request: MutationRequest,
        root_lookup: Callable[[str], ActivityRoot],
        operation: Callable[[ActivityScope], Any],
    ) -> MutationDecision:
        decision = self.authorize(request)
        if not decision.allowed:
            return decision
        assert decision.activity is not None
        try:
            root = root_lookup(decision.activity.activity_id)
            if not isinstance(root, ActivityRoot) or root.activity_id != decision.activity.activity_id or root.fingerprint != decision.activity.fingerprint:
                return MutationDecision("STALE_ACTIVITY_ROOT", False, "adapter returned a different activity root", decision.activity)
            scope = ActivityScope(root.node, decision.activity)
            operation(scope)
        except Exception as exc:  # pragma: no cover - exact adapter failures vary
            return MutationDecision("MUTATION_ERROR", False, _compact_error(str(exc)) or "operation failed", decision.activity)
        if self.telemetry:
            self.telemetry.emit("ACTION", activity_id=decision.activity.activity_id, operation=request.operation, page_generation=request.page_generation)
        return decision


class Cancelable(Protocol):
    state: ResourceState

    def cancel(self, reason: str) -> None: ...


@dataclass
class OwnedTimer:
    target_id: str
    activity_id: str
    page_generation: int
    deadline: float
    state: ResourceState = ResourceState.ACTIVE
    terminal_reason: str | None = None

    def cancel(self, reason: str) -> None:
        if self.state is ResourceState.ACTIVE:
            self.state = ResourceState.CANCELLED
            self.terminal_reason = reason

    def terminal(self, reason: str = "activity_complete") -> None:
        if self.state is ResourceState.ACTIVE:
            self.state = ResourceState.TERMINAL
            self.terminal_reason = reason

    def expire(self, now: float | None = None) -> bool:
        if self.state is ResourceState.ACTIVE and (time.monotonic() if now is None else now) >= self.deadline:
            self.state = ResourceState.EXPIRED
            self.terminal_reason = "deadline_expired"
        return self.state is ResourceState.EXPIRED


@dataclass
class OwnedObserver:
    target_id: str
    activity_id: str
    page_generation: int
    state: ResourceState = ResourceState.ACTIVE
    terminal_reason: str | None = None

    def cancel(self, reason: str) -> None:
        if self.state is ResourceState.ACTIVE:
            self.state = ResourceState.CANCELLED
            self.terminal_reason = reason

    def terminal(self, reason: str = "activity_complete") -> None:
        if self.state is ResourceState.ACTIVE:
            self.state = ResourceState.TERMINAL
            self.terminal_reason = reason


class ActivityResources:
    """Owns all observer/timer resources for one target/activity/generation."""

    def __init__(self, target_id: str, activity_id: str, page_generation: int) -> None:
        self.target_id = target_id
        self.activity_id = activity_id
        self.page_generation = page_generation
        self.closed = False
        self.resources: list[Cancelable] = []

    def add(self, resource: Cancelable) -> None:
        owner = (resource.target_id, resource.activity_id, resource.page_generation)
        expected = (self.target_id, self.activity_id, self.page_generation)
        if owner != expected:
            raise ValueError("resource ownership does not match activity scope")
        if self.closed:
            resource.cancel("activity_resources_already_closed")
        else:
            self.resources.append(resource)

    def close(self, reason: str) -> None:
        if not self.closed:
            for resource in self.resources:
                resource.cancel(reason)
            self.closed = True

    def complete(self) -> None:
        if not self.closed:
            for resource in self.resources:
                if hasattr(resource, "terminal"):
                    resource.terminal("activity_complete")
            self.closed = True

    def generation_changed(self, generation: int) -> None:
        self.close("page_generation_changed" if generation != self.page_generation else "target_changed")

    def deadline_expired(self, now: float) -> None:
        for resource in self.resources:
            if isinstance(resource, OwnedTimer):
                resource.expire(now)
        if self.resources and all(resource.state is not ResourceState.ACTIVE for resource in self.resources):
            self.closed = True


class RetryMachine:
    def __init__(self, telemetry: Telemetry | None = None) -> None:
        self.state = RetryState.INSPECT
        self.attempts = 0
        self._first_failure: tuple[str | None, str | None] | None = None
        self._escalated = False
        self.telemetry = telemetry

    def classify(self, kind: ActivityKind) -> RetryState:
        if self.state is not RetryState.INSPECT:
            raise RuntimeError("classification is only valid from INSPECT")
        self.state = RetryState.CLASSIFY
        if kind is ActivityKind.KNOWN_SAFE_ACTIVITY:
            self.state = RetryState.READY
        else:
            self.state = RetryState.BLOCKED
        return self.state

    def begin_attempt(self, evidence_key: str | None = None) -> int:
        if self.state is RetryState.READY:
            self.attempts = 1
            self.state = RetryState.ATTEMPT_1
        elif self.state is RetryState.DIAGNOSE:
            if self.attempts != 1 or evidence_key is None or evidence_key == self._first_failure[0]:
                self.state = RetryState.BLOCKED
                raise RuntimeError("attempt 2 requires new evidence")
            self.attempts = 2
            self.state = RetryState.ATTEMPT_2
        else:
            raise RuntimeError("mutation attempt is not permitted in the current state")
        if self.telemetry:
            self.telemetry.emit("RETRY", retry_number=self.attempts, state=self.state.value)
        return self.attempts

    def verify(
        self,
        *,
        success: bool,
        state_after: str | None,
        error: str | None,
        evidence_key: str | None,
    ) -> RetryState:
        if self.state not in (RetryState.ATTEMPT_1, RetryState.ATTEMPT_2):
            raise RuntimeError("verification requires an active attempt")
        self.state = RetryState.VERIFY
        if success:
            self.state = RetryState.RECORD_AND_STOP
            if self.telemetry:
                self.telemetry.emit("VERIFY", retry_number=self.attempts, result="terminal_success", state_after=state_after)
            return self.state
        if self.attempts == 1:
            self._first_failure = (evidence_key, _compact_error(error))
            self.state = RetryState.DIAGNOSE
        else:
            self.state = RetryState.BLOCKED
        if self.telemetry:
            self.telemetry.emit("VERIFY", retry_number=self.attempts, result="failure", state_after=state_after, error=_compact_error(error))
            if self.state is RetryState.BLOCKED:
                self.telemetry.emit("BLOCKED", retry_number=self.attempts, reason="retry_cap_or_equivalent_failure")
        return self.state

    def recommend_specialist(self) -> bool:
        if self._escalated:
            return False
        self._escalated = True
        return True


def _safe_diagnostic_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value) else "redacted"


def _safe_diagnostic_state(values: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        if key not in allowed:
            continue
        if isinstance(value, (bool, int)) or value is None:
            result[key] = value
        elif isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value):
            result[key] = value
    return result


@dataclass(frozen=True)
class DiagnosticBundle:
    timestamp: str
    target_fingerprint: str
    section: str
    activity_id: str
    activity_type: str
    page_generation: int
    state_before: str | None
    requested_operation: str
    state_after: str | None
    active_element_summary: dict[str, Any]
    visibility: bool
    aria_state: dict[str, Any]
    completion_marker: str | None
    error: str | None
    retry_number: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "target_fingerprint": _safe_diagnostic_identifier(self.target_fingerprint),
            "section": _safe_diagnostic_identifier(self.section),
            "activity_id": _safe_diagnostic_identifier(self.activity_id),
            "activity_type": _safe_diagnostic_identifier(self.activity_type),
            "page_generation": self.page_generation,
            "state_before": _safe_diagnostic_identifier(self.state_before),
            "requested_operation": _safe_diagnostic_identifier(self.requested_operation),
            "state_after": _safe_diagnostic_identifier(self.state_after),
            "active_element_summary": _safe_diagnostic_state(self.active_element_summary, {"tag", "role", "type", "disabled", "focused", "checked", "selected", "expanded", "pressed"}),
            "visibility": self.visibility,
            "aria_state": _safe_diagnostic_state(self.aria_state, {"checked", "disabled", "expanded", "selected", "pressed", "hidden", "current"}),
            "completion_marker": _safe_diagnostic_identifier(self.completion_marker),
            "error": _compact_error(self.error),
            "retry_number": self.retry_number,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


_GRAPHICAL_ENV_KEYS = frozenset(
    {
        "DISPLAY",
        "XAUTHORITY",
        "XDG_SESSION_TYPE",
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_RUNTIME_DIR",
        "XDG_CURRENT_DESKTOP",
        "DESKTOP_SESSION",
        "PATH",
    }
)


class CuaEnvironmentError(ValueError):
    """The graphical session environment cannot be trusted or loaded."""


def load_graphical_environment(path: str | Path, uid: int | None = None) -> dict[str, str]:
    """Load only the non-secret graphical environment from a private file."""

    env_path = Path(path)
    try:
        metadata = env_path.stat()
    except OSError as exc:
        raise CuaEnvironmentError("graphical session environment is unavailable") from exc
    expected_uid = os.getuid() if uid is None else uid
    if not env_path.is_file() or metadata.st_uid != expected_uid or metadata.st_mode & 0o077:
        raise CuaEnvironmentError("graphical session environment ownership or mode is unsafe")
    result: dict[str, str] = {}
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CuaEnvironmentError("graphical session environment cannot be read") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            raise CuaEnvironmentError("graphical session environment has an invalid entry")
        key, value = stripped.split("=", 1)
        if key not in _GRAPHICAL_ENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        result[key] = value
    return result


def graphical_environment_diagnostic(environment: dict[str, str]) -> dict[str, bool]:
    """Return presence-only diagnostics; never persist session values."""

    return {
        "display_present": bool(environment.get("DISPLAY")),
        "xauthority_present": bool(environment.get("XAUTHORITY")),
        "x11_session": environment.get("XDG_SESSION_TYPE", "").lower() in {"x11", "xwayland"},
        "session_bus_present": bool(environment.get("DBUS_SESSION_BUS_ADDRESS")),
    }


@dataclass(frozen=True)
class CuaResult:
    status: str
    reason: str | None = None
    prerequisite_key: str = ""

    def __str__(self) -> str:
        return self.status if self.status == "CUA_READY" else f"CUA_UNAVAILABLE: {self.reason}"


class CuaPreflight:
    """Hard gate with a cache preventing unchanged-prerequisite retries."""

    def __init__(self, telemetry: Telemetry | None = None) -> None:
        self._last_unavailable_key: str | None = None
        self._last_result: CuaResult | None = None
        self.telemetry = telemetry

    def check(
        self,
        *,
        display: str | None,
        xauthority: str | None = None,
        session_type: str | None = None,
        dbus_session_bus_address: str | None = None,
        x11_probe: Callable[[str], bool] | None = None,
        xauthority_probe: Callable[[str, str], bool] | None = None,
        screenshot_probe: Callable[[str], bool] | None = None,
        hermes_computer_use_probe: Callable[[], bool] | None = None,
        prerequisite_key: str | None = None,
    ) -> CuaResult:
        key = prerequisite_key or (
            f"display={display or 'missing'}|xauthority={'present' if xauthority else 'missing'}|"
            f"session={session_type or 'missing'}|bus={'present' if dbus_session_bus_address else 'missing'}"
        )
        if key == self._last_unavailable_key and self._last_result is not None:
            return self._last_result
        if not display:
            result = CuaResult("CUA_UNAVAILABLE", "DISPLAY is missing", key)
        elif not xauthority:
            result = CuaResult("CUA_UNAVAILABLE", "XAUTHORITY is missing", key)
        elif session_type and session_type.lower() not in {"x11", "xwayland"}:
            result = CuaResult("CUA_UNAVAILABLE", "session is not X11", key)
        elif xauthority_probe is None:
            result = CuaResult("CUA_UNAVAILABLE", "X11 authorization probe is unavailable", key)
        elif not self._call_bool(xauthority_probe, display, xauthority):
            result = CuaResult("CUA_UNAVAILABLE", "X11 authorization failed for XAUTHORITY", key)
        elif x11_probe is None:
            result = CuaResult("CUA_UNAVAILABLE", "X11 reachability probe is unavailable", key)
        elif not self._call_bool(x11_probe, display):
            result = CuaResult("CUA_UNAVAILABLE", "X11 is unreachable", key)
        elif screenshot_probe is None or not self._call_bool(screenshot_probe, display):
            result = CuaResult("CUA_UNAVAILABLE", "screenshot capability is unavailable", key)
        elif hermes_computer_use_probe is None or not self._call_bool(hermes_computer_use_probe):
            result = CuaResult("CUA_UNAVAILABLE", "Hermes computer-use state is unavailable", key)
        else:
            result = CuaResult("CUA_READY", None, key)
        if self.telemetry:
            self.telemetry.emit("CUA_PREFLIGHT", status=str(result), prerequisite_key=key)
        if result.status != "CUA_READY":
            self._last_unavailable_key = key
            self._last_result = result
        else:
            self._last_unavailable_key = None
            self._last_result = result
        return result

    @staticmethod
    def _call_bool(function: Callable[..., bool], *args: Any) -> bool:
        try:
            return bool(function(*args))
        except Exception:
            return False


@dataclass(frozen=True)
class TransitionRecord:
    seq: int
    timestamp: str
    course: str
    chapter: str
    section: str
    activity_id: str
    activity_type: str
    from_state: str
    to_state: str
    verification: str
    target_fingerprint: str
    page_generation: int

    def __post_init__(self) -> None:
        if self.seq < 1 or self.page_generation < 1:
            raise ValueError("transition sequence and page generation must be positive")
        for name in ("timestamp", "course", "chapter", "section", "activity_id", "activity_type", "from_state", "to_state", "verification", "target_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 160 or "\n" in value or "\r" in value:
                raise ValueError(f"invalid transition field: {name}")
            if re.search(r"(?i)answer|cookie|token|authorization|password|secret|credential|https?://", value):
                raise ValueError(f"transition field is outside the secret-free schema: {name}")
        for name in ("chapter", "section", "activity_id", "activity_type", "from_state", "to_state", "verification", "target_fingerprint"):
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", getattr(self, name)):
                raise ValueError(f"invalid transition identifier: {name}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "course": self.course,
            "chapter": self.chapter,
            "section": self.section,
            "activity_id": self.activity_id,
            "activity_type": self.activity_type,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "verification": self.verification,
            "target_fingerprint": self.target_fingerprint,
            "page_generation": self.page_generation,
        }


class CheckpointIntegrityError(RuntimeError):
    pass


class CheckpointJournal:
    """Append transitions, then derive one monotonic current checkpoint."""

    def __init__(self, journal_path: Path, checkpoint_path: Path | None = None, telemetry: Telemetry | None = None) -> None:
        self.journal_path = Path(journal_path)
        self.checkpoint_path = checkpoint_path or self.journal_path.with_name("checkpoint.json")
        self.telemetry = telemetry

    def _read_records(self) -> list[TransitionRecord]:
        if not self.journal_path.exists():
            return []
        records: list[TransitionRecord] = []
        try:
            lines = self.journal_path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if not line.strip():
                    raise CheckpointIntegrityError("blank line in append-only journal")
                raw = json.loads(line)
                record = TransitionRecord(**raw)
                if record.seq != len(records) + 1:
                    raise CheckpointIntegrityError("journal sequence is not monotonic")
                records.append(record)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CheckpointIntegrityError("journal cannot be read safely") from exc
        return records

    def append(self, record: TransitionRecord) -> dict[str, Any]:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                existing = self._read_records()
                expected_seq = len(existing) + 1
                if record.seq != expected_seq:
                    raise CheckpointIntegrityError(f"expected sequence {expected_seq}, got {record.seq}")
                payload = json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"))
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        checkpoint = self.derive_current()
        assert checkpoint is not None
        self._write_checkpoint(checkpoint)
        if self.telemetry:
            self.telemetry.emit("CHECKPOINT", seq=checkpoint["seq"], state=checkpoint["state"])
        return checkpoint

    def next_sequence(self) -> int:
        return len(self._read_records()) + 1

    def derive_current(self) -> dict[str, Any] | None:
        records = self._read_records()
        if not records:
            return None
        latest = records[-1]
        checkpoint = {
            "schema_version": 1,
            "seq": latest.seq,
            "timestamp": latest.timestamp,
            "course": latest.course,
            "chapter": latest.chapter,
            "section": latest.section,
            "activity_id": latest.activity_id,
            "activity_type": latest.activity_type,
            "state": latest.to_state,
            "last_verified_transition": f"{latest.from_state}->{latest.to_state}",
            "verification": latest.verification,
            "target_fingerprint": latest.target_fingerprint,
            "page_generation": latest.page_generation,
        }
        if self.checkpoint_path.exists():
            try:
                existing = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
                if int(existing.get("seq", 0)) > latest.seq:
                    raise CheckpointIntegrityError("derived checkpoint is newer than its journal")
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise CheckpointIntegrityError("derived checkpoint cannot be read safely") from exc
        return checkpoint

    def _write_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="checkpoint.", suffix=".tmp", dir=self.checkpoint_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(checkpoint, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.checkpoint_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def stable_fingerprint(parts: Iterable[str]) -> str:
    """Hash structural/diagnostic parts without storing their source content."""

    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", "replace"))
        digest.update(b"\0")
    return digest.hexdigest()[:24]
