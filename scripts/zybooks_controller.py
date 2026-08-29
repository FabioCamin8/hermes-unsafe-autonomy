#!/usr/bin/env python3
"""Read-only zyBooks target reconciliation and optional CUA doctor."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_control import (  # noqa: E402
    CuaEnvironmentError,
    TargetReconciler,
    load_graphical_environment,
)
from browser_control.cdp import CdpError, CdpTargetCollector  # noqa: E402


def _reason_for_candidate(candidate: dict[str, Any]) -> str:
    evidence = candidate.get("evidence") or {}
    if evidence.get("target_present") is False:
        return "target absent from current Target.getTargets snapshot"
    if evidence.get("cdp_reachable") is False:
        return "target CDP page session is disconnected"
    if evidence.get("lifecycle_state") == "detached":
        return "target lifecycle is explicitly detached"
    return "explicit target/CDP absence evidence"


def _target_table(report: dict[str, Any]) -> str:
    lines = ["TARGET\tSECTION\tROLE\tSTATE\tGENERATION"]
    reconciliation = report["target_reconciliation"]
    for candidate in reconciliation.get("candidates", []):
        evidence = candidate.get("evidence") or {}
        role = candidate.get("role", "candidate")
        state = "stale" if role == "PROVEN_STALE" else "live"
        lines.append(
            "\t".join(
                (
                    str(candidate.get("target_id", "")),
                    str(candidate.get("section") or "unknown"),
                    role,
                    state,
                    str(evidence.get("document_generation") or "unknown"),
                )
            )
        )
    lines.append(str(reconciliation.get("status", "AMBIGUOUS_TARGET")))
    for reason in reconciliation.get("reasons", []):
        lines.append(f"REASON\t{reason}")
    custom_count = 0
    for target in report.get("targets", []):
        document = target.get("document") or {}
        for activity in document.get("activities", []):
            if activity.get("custom_interaction_candidate"):
                custom_count += 1
                lines.append(
                    f"CUSTOM_INTERACTION_CANDIDATE\tactivity_id={activity.get('activity_id')}\tsignals={','.join(activity.get('custom_interaction_signals', []))}"
                )
    lines.append(f"CUSTOM_INTERACTION_CANDIDATES\t{custom_count}")
    return "\n".join(lines)


def run_targets(args: argparse.Namespace) -> int:
    collector: CdpTargetCollector | None = None
    try:
        collector = CdpTargetCollector(args.cdp_url)
        report, reconciliation = collector.collect(args.section)
        cleanup: dict[str, Any] = {"requested": bool(args.close_proven_stale_targets), "closed_target_ids": []}
        if args.close_proven_stale_targets:
            if reconciliation.canonical is None:
                cleanup["error"] = "cleanup requires a canonical target proven by lifecycle evidence"
            else:
                for candidate in reconciliation.proven_stale:
                    print(
                        f"WILL_CLOSE target={candidate.target_id} reason={_reason_for_candidate(candidate)}",
                        file=sys.stderr,
                    )
                try:
                    cleanup["closed_target_ids"] = list(
                        TargetReconciler.close_proven_stale_targets(reconciliation, collector.close_target)
                    )
                except ValueError as exc:
                    cleanup["error"] = str(exc)
        report["cleanup"] = cleanup
        if args.json:
            print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        else:
            print(_target_table(report))
            print(f"TARGETS_INSPECTED\t{report['target_count']}")
            print(f"COURSE_MUTATIONS\t{report['course_mutations']}")
        return 0
    except (CdpError, OSError, TypeError, ValueError) as exc:
        payload = {"status": "CDP_UNAVAILABLE", "reason": type(exc).__name__}
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"CDP_UNAVAILABLE: {payload['reason']}")
        return 2
    finally:
        if collector is not None:
            collector.close()


def _doctor_reason(output: str, exit_code: int) -> str | None:
    lowered = output.lower()
    if "x11 is not reachable" in lowered or "display" in lowered and "missing" in lowered:
        return "X11 is unreachable from the CUA process"
    if "screen_capture_capability" in lowered and "❌" in output:
        return "screenshot capability is unavailable"
    if "ax_capability" in lowered and "❌" in output:
        return "accessibility/UI inspection is unavailable"
    if exit_code:
        return "Hermes Computer Use doctor failed"
    if "cua-driver" not in lowered or "— ok" not in output and "- ok" not in output:
        return "Hermes Computer Use doctor reported degraded"
    return None


def run_cua_doctor(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    default_path = Path.home() / ".config" / "hermes-unsafe-vm" / "graphical-session.env"
    env_path = Path(args.graphical_env_file) if args.graphical_env_file else default_path
    try:
        loaded = load_graphical_environment(env_path)
    except CuaEnvironmentError as exc:
        result = {"status": "CUA_UNAVAILABLE", "reason": str(exc), "environment": {}}
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print(f"CUA_UNAVAILABLE: {result['reason']}")
        return 0
    env.update(loaded)
    command = shlex.split(args.doctor_command)
    if not command:
        print("CUA_UNAVAILABLE: doctor command is empty")
        return 0
    completed = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    output = (completed.stdout + completed.stderr).strip()
    reason = _doctor_reason(output, completed.returncode)
    result = {
        "status": "CUA_READY" if reason is None else "CUA_UNAVAILABLE",
        "reason": reason,
        "environment": {
            "display_present": bool(env.get("DISPLAY")),
            "xauthority_present": bool(env.get("XAUTHORITY")),
            "x11_session": env.get("XDG_SESSION_TYPE", "").lower() in {"x11", "xwayland"},
            "session_bus_present": bool(env.get("DBUS_SESSION_BUS_ADDRESS")),
            "source": "private graphical session environment",
        },
        "doctor_exit_code": completed.returncode,
    }
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        if output:
            print(output)
        print(result["status"] if reason is None else f"CUA_UNAVAILABLE: {reason}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    targets = subparsers.add_parser("targets", help="inspect current Chromium targets without foregrounding")
    targets.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    targets.add_argument("--section", default="2.5")
    targets.add_argument("--json", action="store_true")
    targets.add_argument("--close-proven-stale-targets", action="store_true")
    targets.set_defaults(handler=run_targets)
    cua = subparsers.add_parser("cua-doctor", help="run the CUA doctor with a private graphical session environment")
    cua.add_argument("--graphical-env-file")
    cua.add_argument("--doctor-command", default="hermes computer-use doctor")
    cua.add_argument("--json", action="store_true")
    cua.set_defaults(handler=run_cua_doctor)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
