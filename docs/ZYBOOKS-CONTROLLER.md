# Deterministic zyBooks controller

This package is a fail-closed browser-control foundation for read-only
inspection and explicitly authorized, activity-scoped operations. It does not
solve, generate, reveal, fill, check, submit, or navigate coursework.

## Ownership model

`TargetSelector` accepts candidates identified by target ID, sanitized URL
path, course/chapter/section, title, DOM section heading, and page fingerprint.
It returns `NO_TARGET`, `TARGET_SELECTED`, or `AMBIGUOUS_TARGET`. Only the
single-candidate result permits a mutation scope. Focus, visibility, tab order,
and recency are not selection criteria.

`TargetReconciler` adds browser-level `TargetEvidence`: target type, browser
context, opener/frame relationship, CDP reachability, window mapping,
navigation entry and loader identity, document readiness/lifecycle signals,
visibility/focus diagnostics, and safe document timing. It selects a canonical
target only when exactly one live page remains or every other eligible target
is explicitly absent/disconnected/detached. Progress, focus, visibility,
recency, DOM fingerprints, and opener relationships never prove authority.
The read-only command is:

```bash
python3 scripts/zybooks_controller.py targets --section 2.5 --json
```

It reports `TARGET_SELECTED` or `AMBIGUOUS_TARGET` and never foregrounds,
navigates, reloads, or mutates a page. Optional `--close-proven-stale-targets`
first prints each target and lifecycle reason to stderr and can close only a
target classified `PROVEN_STALE`; it requires a canonical target and cannot
close that target.

The browser-side `observer.js` is a one-shot DOM inventory function. It
enumerates `.interactive-activity-container` roots and reports control types,
ARIA roles, iframe/gesture markers, completion markers, scoped Check/Submit
counts, visibility, and structural fingerprints. It also emits
`custom_interaction_candidate` plus structural signals for sortable/term-bank,
roles, pointer handlers, keyboard-reorder semantics, draggable markers,
canvas, and gesture-marked SVG. It never reads answer text,
input values, cookies, headers, or full HTML.

`ActivityClassifier` gives protected signals precedence:

| Result | Signals |
| --- | --- |
| `PROTECTED_CHALLENGE` | challenge/assessment markers |
| `PROTECTED_LAB` | zyLab/editor/grader markers |
| `PROTECTED_DRAG_AND_DROP` | native draggable, sortable/term-bank, drag/drop roles, pointer handlers, canvas, or SVG with gesture indicators |
| `UNKNOWN` | iframe, unsupported/custom controls, missing participation contract, or insufficient evidence |
| `KNOWN_SAFE_ACTIVITY` | only the small recognized radio/checkbox/animation contract |

Protected and unknown roots cannot enter a generic handler. Every mutation
request carries the selected target ID, page generation, activity ID, root
fingerprint, and operation. `ProtectedContainerRegistry` rejects ambiguous
targets, cross-activity roots, protected/unknown activities, and stale
generations. `ActivityScope` exposes only root-relative selectors, so a
`button.check-button` lookup cannot escape to another activity.

## Generation, retries, and resources

`PageGenerationTracker` increments the generation on first observation, target
change, or page fingerprint change. Old requests return
`STALE_PAGE_GENERATION`; callers reacquire the root rather than retaining DOM
references.

`RetryMachine` implements `INSPECT -> CLASSIFY -> READY -> ATTEMPT_1 -> VERIFY
-> DIAGNOSE -> ATTEMPT_2 -> VERIFY -> RECORD_AND_STOP/BLOCKED`. Attempt 2
requires a new evidence key and the machine has no third attempt. The one
specialist escalation recommendation is separately capped. `ActivityResources`
owns every observer/timer by target, activity, generation, and deadline; it
closes resources on completion, target/generation change, or deadline.

## Diagnostics and checkpoints

`DiagnosticBundle` is compact and secret-free: it contains state summaries,
ARIA state, visibility, completion marker, fingerprints, and a redacted error,
but no answer text, input values, cookies, tokens, credentials, or HTML.

`CheckpointJournal` appends typed transition records under an exclusive file
lock and atomically derives one current checkpoint. Sequence numbers must be
strictly monotonic. The journal is authoritative; a derived checkpoint newer
than the journal is an integrity error. Runtime journal/checkpoint paths must
remain private and outside a public source tree.

## CUA gate

`CuaPreflight` returns exactly `CUA_READY` or
`CUA_UNAVAILABLE: <reason>` after checking DISPLAY, X11 reachability,
screenshot capability, X11 authorization, and Hermes computer-use readiness.
DISPLAY alone is insufficient: the caller must provide the private
Xauthority/session environment and an authorization probe. An unavailable
result is cached by a prerequisite key, so unchanged prerequisites do not
trigger another CUA request. CUA remains a fallback after DOM inspection.

On the graphical Hermes VM, use the existing least-privilege bridge before
the doctor or controller command:

```bash
hermes-graphical hermes computer-use doctor
hermes-graphical python3 scripts/zybooks_controller.py cua-doctor
```

The bridge inherits the existing user-session `DISPLAY`, `XAUTHORITY`, DBus,
and X11 values; it does not use `xhost`, TCP X11, or a privileged daemon.

## Existing userscript audit

The requested `zybooks-solver.js` v1.0.2 was inspected without executing it.
It uses document-global radio, checkbox, start, show-answer, question, and
Check selectors; launches delayed nested timers; polls Play/Pause globally;
reads answer-bearing elements; and clicks every global Check button. It has no
target identity, activity ownership, protected-container registry, generation
check, stale-node handling, terminal verification, or finite retry policy.

This package is an isolation replacement, not an extension of that solver.
The new observer contains no `.click()`, answer extraction, fill, submit, or
document-global mutation loop.
