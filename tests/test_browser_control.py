from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from browser_control import (
    ActivityClassifier,
    ActivityKind,
    ActivityRecord,
    ActivityRoot,
    ActivityResources,
    CuaEnvironmentError,
    CheckpointJournal,
    CuaPreflight,
    DiagnosticBundle,
    MutationRequest,
    OwnedObserver,
    OwnedTimer,
    PageGenerationTracker,
    ProtectedContainerRegistry,
    RetryMachine,
    RetryState,
    TargetCandidate,
    TargetEvidence,
    TargetReconciler,
    TargetSelectionStatus,
    TargetSelector,
    TransitionRecord,
    load_graphical_environment,
)


def record(index: int = 1, **overrides: object) -> ActivityRecord:
    values: dict[str, object] = {
        "activity_index": index,
        "activity_id": f"2.5.{index}",
        "section": "2.5",
        "participation_marker": True,
        "major_control_types": ("radio",),
        "visible": True,
        "fingerprint": f"fingerprint-{index}",
    }
    values.update(overrides)
    return ActivityRecord(**values)


def sortable_fixture_record() -> ActivityRecord:
    return record(
        activity_id="fixture.matching",
        section="fixture",
        fingerprint="fixture-matching-fingerprint",
        sortable_container_count=3,
        sortable_item_count=2,
        term_bank_container_count=1,
        focusable_custom_item_count=2,
        non_native_draggable_count=2,
        drag_drop_roles=("option",),
        keyboard_reorder_markers=("keyboard_focusable_reorder",),
    )


class Root:
    def __init__(self, name: str) -> None:
        self.name = name
        self.queries: list[str] = []

    def query_selector(self, selector: str) -> str:
        self.queries.append(selector)
        return f"{self.name}:{selector}"

    def query_selector_all(self, selector: str) -> tuple[str, ...]:
        self.queries.append(selector)
        return (f"{self.name}:{selector}",)


class BrowserControlTests(unittest.TestCase):
    def test_zero_targets_returns_no_target(self) -> None:
        selection = TargetSelector().select([])
        self.assertEqual(selection.status, TargetSelectionStatus.NO_TARGET)
        self.assertFalse(selection.mutations_allowed)

    def test_target_ambiguity_fails_closed(self) -> None:
        candidates = [
            TargetCandidate("tab-a", "https://learn.zybooks.com/zybook/course/chapter/2/section/5", page_fingerprint="a"),
            TargetCandidate("tab-b", "https://learn.zybooks.com/zybook/course/chapter/2/section/5", page_fingerprint="b"),
        ]
        selection = TargetSelector().select(candidates)
        self.assertEqual(selection.status, TargetSelectionStatus.AMBIGUOUS_TARGET)
        self.assertFalse(selection.mutations_allowed)

    def test_reconciliation_duplicate_targets_with_no_proof_is_ambiguous(self) -> None:
        candidates = [
            TargetCandidate(
                "tab-a",
                "https://learn.zybooks.com/section/2.5",
                section="2.5",
                page_fingerprint="a",
                evidence=TargetEvidence(window_id=1, cdp_reachable=True, visibility_state="visible", has_focus=True, completion_percentage=90.0),
            ),
            TargetCandidate(
                "tab-b",
                "https://learn.zybooks.com/section/2.5",
                section="2.5",
                page_fingerprint="b",
                evidence=TargetEvidence(window_id=1, cdp_reachable=True, visibility_state="hidden", has_focus=False, completion_percentage=10.0),
            ),
        ]
        result = TargetReconciler().reconcile(candidates)
        self.assertEqual(result.status, TargetSelectionStatus.AMBIGUOUS_TARGET)
        self.assertIsNone(result.canonical)
        self.assertEqual(result.role_for("tab-a"), "candidate")
        self.assertEqual(result.role_for("tab-b"), "candidate")
        self.assertIn("focus", " ".join(result.reasons))
        self.assertEqual(result.candidates[0].evidence.completion_percentage, 90.0)  # type: ignore[union-attr]

    def test_progress_mismatch_does_not_decide_authority(self) -> None:
        candidates = [
            TargetCandidate("tab-a", "https://learn.zybooks.com/section/2.5", section="2.5", page_fingerprint="more-complete"),
            TargetCandidate("tab-b", "https://learn.zybooks.com/section/2.5", section="2.5", page_fingerprint="less-complete"),
        ]
        result = TargetReconciler().reconcile(candidates)
        self.assertEqual(result.status, TargetSelectionStatus.AMBIGUOUS_TARGET)

    def test_focus_mismatch_does_not_decide_authority(self) -> None:
        candidates = [
            TargetCandidate("tab-a", "https://learn.zybooks.com/section/2.5", section="2.5", evidence=TargetEvidence(has_focus=True)),
            TargetCandidate("tab-b", "https://learn.zybooks.com/section/2.5", section="2.5", evidence=TargetEvidence(has_focus=False)),
        ]
        result = TargetReconciler().reconcile(candidates)
        self.assertEqual(result.status, TargetSelectionStatus.AMBIGUOUS_TARGET)

    def test_proven_stale_target_is_separate_and_safe_to_reconcile(self) -> None:
        live = TargetCandidate(
            "live",
            "https://learn.zybooks.com/section/2.5",
            section="2.5",
            evidence=TargetEvidence(target_present=True, cdp_reachable=True),
        )
        stale = TargetCandidate(
            "stale",
            "https://learn.zybooks.com/section/2.5",
            section="2.5",
            evidence=TargetEvidence(target_present=False, cdp_reachable=False),
        )
        result = TargetReconciler().reconcile([live, stale])
        self.assertEqual(result.status, TargetSelectionStatus.TARGET_SELECTED)
        self.assertEqual(result.canonical, live)
        self.assertEqual(result.role_for("stale"), "PROVEN_STALE")
        closed: list[str] = []
        self.assertEqual(TargetReconciler.close_proven_stale_targets(result, lambda target_id: closed.append(target_id) or True), ("stale",))
        self.assertEqual(closed, ["stale"])

    def test_stale_cleanup_requires_canonical_target(self) -> None:
        result = TargetReconciler().reconcile([
            TargetCandidate("stale", "https://learn.zybooks.com/section/2.5", section="2.5", evidence=TargetEvidence(target_present=False, cdp_reachable=False)),
        ])
        with self.assertRaises(ValueError):
            TargetReconciler.close_proven_stale_targets(result, lambda _: True)

    def test_ambiguous_protected_and_unknown_targets_have_zero_mutations(self) -> None:
        candidates = [
            TargetCandidate("tab-a", "https://learn.zybooks.com/zybook/course/chapter/2/section/5"),
            TargetCandidate("tab-b", "https://learn.zybooks.com/zybook/course/chapter/2/section/5"),
        ]
        selection = TargetSelector().select(candidates)
        registry = ProtectedContainerRegistry(selection)
        registry.bind("tab-a", 1, [record(1, sortable_markers=("sortable",)), record(2, challenge_markers=("challenge",)), record(3, participation_marker=False, major_control_types=("custom",))])
        calls: list[str] = []
        for activity_id in ("2.5.1", "2.5.2", "2.5.3"):
            request = MutationRequest("tab-a", 1, activity_id, f"fingerprint-{int(activity_id[-1])}", "click_check")
            decision = registry.perform(request, lambda _: calls.append(activity_id), lambda _: calls.append(activity_id))
            self.assertFalse(decision.allowed)
        self.assertEqual(calls, [])

    def test_classifier_protects_drag_drop(self) -> None:
        activity = record(sortable_markers=("sortable_marker",), term_bank_markers=("term_bank_marker",))
        self.assertEqual(ActivityClassifier().classify(activity), ActivityKind.PROTECTED_DRAG_AND_DROP)

    def test_historical_sortable_fixture_gets_specific_protected_classification(self) -> None:
        fixture = (Path(__file__).parent / "fixtures" / "sanitized_matching_widget.html").read_text(encoding="utf-8")
        for marker in (
            'class="zb-sortable-container term-bank"',
            'class="zb-sortable-item definition-match-term"',
            'role="option"',
            'tabindex="0"',
            'draggable="false"',
        ):
            self.assertIn(marker, fixture)

        classified = ActivityClassifier().inventory([sortable_fixture_record()])[0]
        self.assertEqual(classified.kind, ActivityKind.PROTECTED_SORTABLE_MATCHING)
        diagnostic = classified.diagnostic()
        self.assertEqual(diagnostic["custom_interaction_signals"][:4], [
            "sortable_container",
            "sortable_item",
            "term_bank",
            "focusable_custom_item",
        ])
        self.assertEqual(diagnostic["non_native_draggable"], 2)
        self.assertEqual(diagnostic["keyboard_contract"], "observed")
        self.assertFalse(diagnostic["mutation_allowed"])
        self.assertNotIn("ITEM_A", json.dumps(diagnostic))

    def test_sortable_markers_without_matching_structure_remain_generic_protected(self) -> None:
        activity = record(sortable_container_count=1, sortable_item_count=1)
        self.assertEqual(ActivityClassifier().classify(activity), ActivityKind.PROTECTED_DRAG_AND_DROP)

    def test_non_native_draggable_marker_alone_is_not_a_custom_signal(self) -> None:
        activity = record(non_native_draggable_count=3)
        self.assertEqual(ActivityClassifier().classify(activity), ActivityKind.KNOWN_SAFE_ACTIVITY)
        self.assertEqual(ActivityClassifier().custom_interaction_candidates([activity]), ())
        self.assertEqual(activity.diagnostic()["non_native_draggable"], 3)

    def test_custom_interaction_candidates_are_read_only(self) -> None:
        activity = record(
            drag_drop_roles=("option",),
            keyboard_reorder_markers=("keyboard_focusable_reorder",),
        )
        candidates = ActivityClassifier().custom_interaction_candidates([activity])
        self.assertEqual(candidates[0]["activity_id"], "2.5.1")
        self.assertEqual(candidates[0]["classification"], ActivityKind.PROTECTED_DRAG_AND_DROP.value)
        self.assertIn("role:option", candidates[0]["signals"])
        self.assertIn("keyboard:keyboard_focusable_reorder", candidates[0]["signals"])
        self.assertFalse(candidates[0]["mutation_allowed"])

    def test_decorative_svg_does_not_imply_gesture_control(self) -> None:
        self.assertEqual(ActivityClassifier().classify(record(svg_count=3)), ActivityKind.KNOWN_SAFE_ACTIVITY)

    def test_pointer_marked_svg_is_protected(self) -> None:
        activity = record(svg_count=1, pointer_markers=("pointer_handler",))
        self.assertEqual(ActivityClassifier().classify(activity), ActivityKind.PROTECTED_DRAG_AND_DROP)

    def test_classifier_protects_challenge(self) -> None:
        self.assertEqual(ActivityClassifier().classify(record(challenge_markers=("challenge_marker",))), ActivityKind.PROTECTED_CHALLENGE)

    def test_classifier_protects_lab(self) -> None:
        self.assertEqual(ActivityClassifier().classify(record(lab_markers=("editor_marker",))), ActivityKind.PROTECTED_LAB)

    def test_unknown_widget_never_becomes_safe(self) -> None:
        activity = record(participation_marker=False, major_control_types=("custom_widget",))
        self.assertEqual(ActivityClassifier().classify(activity), ActivityKind.UNKNOWN)

    def test_cross_activity_check_lookup_is_scoped(self) -> None:
        candidate = TargetCandidate("tab-a", "https://learn.zybooks.com/zybook/course/chapter/2/section/5")
        selection = TargetSelector().select([candidate])
        activity_a = record(1)
        activity_b = record(2)
        registry = ProtectedContainerRegistry(selection)
        registry.bind("tab-a", 1, [activity_a, activity_b])
        roots = {
            "2.5.1": ActivityRoot("2.5.1", "fingerprint-1", Root("A")),
            "2.5.2": ActivityRoot("2.5.2", "fingerprint-2", Root("B")),
        }
        request = MutationRequest("tab-a", 1, "2.5.1", "fingerprint-1", "click_check", "button.check-button")
        selected: list[str] = []

        def operation(scope: object) -> None:
            selected.append(scope.query_selector("button.check-button"))  # type: ignore[attr-defined]

        decision = registry.perform(request, roots.__getitem__, operation)
        self.assertTrue(decision.allowed)
        self.assertEqual(selected, ["A:button.check-button"])
        self.assertEqual(roots["2.5.2"].node.queries, [])

    def test_sanitized_sortable_fixture_isolated_from_global_mutation_paths(self) -> None:
        fixture = (Path(__file__).parent / "fixtures" / "sanitized_matching_widget.html").read_text(encoding="utf-8")
        self.assertIn("check-button", fixture)
        self.assertIn("submit-button", fixture)

        candidate = TargetCandidate("fixture-target", "https://example.test/fixture")
        registry = ProtectedContainerRegistry(TargetSelector().select([candidate]))
        protected = sortable_fixture_record()
        safe = record(2, activity_id="fixture.safe", section="fixture")
        registry.bind("fixture-target", 1, [protected, safe])
        protected_root = Root("protected")
        safe_root = Root("safe")
        roots = {protected.activity_id: ActivityRoot(protected.activity_id, protected.fingerprint, protected_root), safe.activity_id: ActivityRoot(safe.activity_id, safe.fingerprint, safe_root)}
        root_lookups: list[str] = []
        mutations: list[str] = []
        started_resource_owners: list[str] = []
        protected_snapshot = protected.diagnostic()

        def root_lookup(activity_id: str) -> ActivityRoot:
            root_lookups.append(activity_id)
            return roots[activity_id]

        def process_safe(scope: object) -> None:
            mutations.append(scope.query_selector("button.check-button"))  # type: ignore[attr-defined]
            started_resource_owners.append(safe.activity_id)

        protected_request = MutationRequest("fixture-target", 1, protected.activity_id, protected.fingerprint, "protected_probe")
        decision = registry.perform(protected_request, root_lookup, process_safe)
        self.assertEqual(decision.code, "PROTECTED_ACTIVITY")
        self.assertFalse(decision.allowed)
        self.assertEqual(root_lookups, [])
        self.assertEqual(protected_root.queries, [])
        self.assertEqual(mutations, [])
        self.assertEqual(started_resource_owners, [])
        self.assertEqual(protected.diagnostic(), protected_snapshot)

        safe_request = MutationRequest("fixture-target", 1, safe.activity_id, safe.fingerprint, "safe_probe")
        decision = registry.perform(safe_request, root_lookup, process_safe)
        self.assertTrue(decision.allowed)
        self.assertEqual(root_lookups, [safe.activity_id])
        self.assertEqual(safe_root.queries, ["button.check-button"])
        self.assertEqual(mutations, ["safe:button.check-button"])
        self.assertEqual(started_resource_owners, [safe.activity_id])

        safe_resources = ActivityResources("fixture-target", safe.activity_id, 1)
        safe_timer = OwnedTimer("fixture-target", safe.activity_id, 1, deadline=100.0)
        safe_resources.add(safe_timer)
        self.assertEqual([(resource.activity_id, resource.state.value) for resource in safe_resources.resources], [(safe.activity_id, "ACTIVE")])
        self.assertEqual(protected_root.queries, [])

    def test_adapter_cannot_swap_activity_roots(self) -> None:
        candidate = TargetCandidate("tab-a", "https://learn.zybooks.com/zybook/course/chapter/2/section/5")
        selection = TargetSelector().select([candidate])
        registry = ProtectedContainerRegistry(selection)
        registry.bind("tab-a", 1, [record(1)])
        request = MutationRequest("tab-a", 1, "2.5.1", "fingerprint-1", "click_check")
        called: list[bool] = []
        wrong_root = ActivityRoot("2.5.2", "fingerprint-2", Root("B"))
        decision = registry.perform(request, lambda _: wrong_root, lambda _: called.append(True))
        self.assertEqual(decision.code, "STALE_ACTIVITY_ROOT")
        self.assertEqual(called, [])

    def test_rerender_rejects_old_generation(self) -> None:
        tracker = PageGenerationTracker()
        first = tracker.observe("tab-a", "page-a")
        second = tracker.observe("tab-a", "page-b")
        self.assertTrue(first.changed)
        self.assertTrue(second.changed)
        candidate = TargetCandidate("tab-a", "https://example.test")
        selection = TargetSelector().select([candidate])
        registry = ProtectedContainerRegistry(selection)
        registry.bind("tab-a", second.generation, [record()])
        request = MutationRequest("tab-a", first.generation, "2.5.1", "fingerprint-1", "click_check")
        self.assertEqual(registry.authorize(request).code, "STALE_PAGE_GENERATION")

    def test_target_lifecycle_replacement_is_not_authoritative(self) -> None:
        old = TargetCandidate(
            "tab-a",
            "https://learn.zybooks.com/section/2.5",
            section="2.5",
            evidence=TargetEvidence(loader_id="old-loader", document_generation="loader:old", cdp_reachable=True),
        )
        replacement = TargetCandidate(
            "tab-a",
            "https://learn.zybooks.com/section/2.5",
            section="2.5",
            evidence=TargetEvidence(loader_id="new-loader", document_generation="loader:new", cdp_reachable=True),
        )
        result = TargetReconciler().reconcile([old, replacement])
        self.assertEqual(result.status, TargetSelectionStatus.AMBIGUOUS_TARGET)
        self.assertIsNone(result.canonical)

    def test_terminal_activity_cleans_owned_resources(self) -> None:
        resources = ActivityResources("tab-a", "2.5.1", 1)
        timer = OwnedTimer("tab-a", "2.5.1", 1, deadline=100.0)
        observer = OwnedObserver("tab-a", "2.5.1", 1)
        resources.add(timer)
        resources.add(observer)
        resources.complete()
        self.assertEqual(timer.state.value, "TERMINAL")
        self.assertEqual(observer.state.value, "TERMINAL")
        self.assertTrue(resources.closed)

    def test_retry_cap_blocks_third_mutation(self) -> None:
        retry = RetryMachine()
        self.assertEqual(retry.classify(ActivityKind.KNOWN_SAFE_ACTIVITY), RetryState.READY)
        self.assertEqual(retry.begin_attempt("before-a"), 1)
        self.assertEqual(retry.verify(success=False, state_after="unchanged", error="no transition", evidence_key="failure-a"), RetryState.DIAGNOSE)
        self.assertTrue(retry.recommend_specialist())
        self.assertFalse(retry.recommend_specialist())
        self.assertEqual(retry.begin_attempt("new-dom-evidence"), 2)
        self.assertEqual(retry.verify(success=False, state_after="unchanged", error="no transition", evidence_key="failure-b"), RetryState.BLOCKED)
        with self.assertRaises(RuntimeError):
            retry.begin_attempt("third-evidence")

    def test_attempt_two_requires_new_evidence(self) -> None:
        retry = RetryMachine()
        retry.classify(ActivityKind.KNOWN_SAFE_ACTIVITY)
        retry.begin_attempt("before")
        retry.verify(success=False, state_after="unchanged", error="same", evidence_key="same-evidence")
        with self.assertRaises(RuntimeError):
            retry.begin_attempt("same-evidence")
        self.assertEqual(retry.state, RetryState.BLOCKED)

    def test_cua_missing_display_is_cached_without_retry(self) -> None:
        gate = CuaPreflight()
        calls = []
        first = gate.check(display=None, x11_probe=lambda _: calls.append("x11") or True)
        second = gate.check(display=None, x11_probe=lambda _: calls.append("x11") or True)
        self.assertEqual(str(first), "CUA_UNAVAILABLE: DISPLAY is missing")
        self.assertEqual(str(second), "CUA_UNAVAILABLE: DISPLAY is missing")
        self.assertEqual(calls, [])

    def test_cua_requires_all_capabilities(self) -> None:
        gate = CuaPreflight()
        result = gate.check(
            display=":0",
            xauthority="/home/hermes/.Xauthority",
            xauthority_probe=lambda _display, _xauthority: True,
            x11_probe=lambda _: True,
            screenshot_probe=lambda _: True,
            hermes_computer_use_probe=lambda: False,
            prerequisite_key="display=:0|state=not-ready",
        )
        self.assertEqual(str(result), "CUA_UNAVAILABLE: Hermes computer-use state is unavailable")

    def test_cua_wrong_xauthority_is_a_hard_failure(self) -> None:
        result = CuaPreflight().check(
            display=":0",
            xauthority="/tmp/wrong.Xauthority",
            xauthority_probe=lambda _display, _xauthority: False,
            x11_probe=lambda _: True,
            screenshot_probe=lambda _: True,
            hermes_computer_use_probe=lambda: True,
        )
        self.assertEqual(str(result), "CUA_UNAVAILABLE: X11 authorization failed for XAUTHORITY")

    def test_cua_healthy_requires_authorized_x11_session(self) -> None:
        result = CuaPreflight().check(
            display=":0",
            xauthority="/home/hermes/.Xauthority",
            session_type="x11",
            dbus_session_bus_address="unix:path=/run/user/1000/bus",
            xauthority_probe=lambda _display, _xauthority: True,
            x11_probe=lambda _: True,
            screenshot_probe=lambda _: True,
            hermes_computer_use_probe=lambda: True,
        )
        self.assertEqual(str(result), "CUA_READY")

    def test_graphical_environment_requires_private_owner_only_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graphical-session.env"
            path.write_text("DISPLAY=:0\nXAUTHORITY=/home/hermes/.Xauthority\nXDG_SESSION_TYPE=x11\n", encoding="utf-8")
            path.chmod(0o600)
            self.assertEqual(load_graphical_environment(path)["DISPLAY"], ":0")
            path.chmod(0o644)
            with self.assertRaises(CuaEnvironmentError):
                load_graphical_environment(path)

    def test_checkpoint_is_journal_authority_and_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = CheckpointJournal(root / "transitions.jsonl", root / "checkpoint.json")
            first = TransitionRecord(1, "2026-08-28T00:00:00Z", "course", "2", "2.5", "2.5.1", "participation_animation", "incomplete", "complete", "completion_marker_observed", "fp-a", 1)
            second = TransitionRecord(2, "2026-08-28T00:01:00Z", "course", "2", "2.5", "2.5.2", "participation_multiple_choice", "incomplete", "blocked", "two_attempts_exhausted", "fp-a", 1)
            journal.append(first)
            checkpoint = journal.append(second)
            self.assertEqual(checkpoint["seq"], 2)
            self.assertEqual(checkpoint["state"], "blocked")
            self.assertEqual(json.loads((root / "checkpoint.json").read_text())["seq"], 2)
            with self.assertRaises(Exception):
                journal.append(TransitionRecord(4, "2026-08-28T00:02:00Z", "course", "2", "2.5", "2.5.3", "unknown", "incomplete", "unknown", "unknown_activity", "fp-a", 1))

    def test_diagnostic_bundle_has_no_page_content(self) -> None:
        bundle = DiagnosticBundle(
            "2026-08-28T00:00:00Z", "fp-a", "2.5", "2.5.1", "participation_animation", 1,
            "incomplete", "click_check", "unchanged", {"tag": "button", "disabled": False}, True,
            {"checked": False, "expanded": False}, None, "authorization=secret", 1,
        )
        payload = bundle.to_dict()
        self.assertNotIn("secret", bundle.to_json())
        self.assertNotIn("answer", json.dumps(payload).lower())

    def test_observer_is_not_a_solver_or_global_mutator(self) -> None:
        observer = (Path(__file__).parents[1] / "browser_control" / "observer.js").read_text(encoding="utf-8")
        self.assertNotIn(".click(", observer)
        self.assertNotIn(".dispatchEvent(", observer)
        self.assertNotIn("document.querySelector(\"button.check-button\")", observer)
        self.assertNotIn("innerText", observer)
        self.assertNotIn(".value", observer)
        self.assertNotIn("setInterval", observer)
        self.assertIn("custom_interaction_candidate", observer)


if __name__ == "__main__":
    unittest.main()
