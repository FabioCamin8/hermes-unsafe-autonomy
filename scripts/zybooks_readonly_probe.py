#!/usr/bin/env python3
"""Read-only zyBooks inventory through the configured Chrome MCP server."""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import subprocess
from pathlib import Path
from typing import Any, Iterable

from browser_control import (
    ActivityClassifier,
    ActivityRecord,
    CuaPreflight,
    TargetCandidate,
    TargetSelector,
)
from scripts.mcp_probe import page_id_candidates, read_response, send_request, tool_call


def _text_blocks(result: dict[str, Any]) -> Iterable[str]:
    for block in result.get("content", []):
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            yield block["text"]


def _page_ids(result: dict[str, Any]) -> list[int]:
    listing = "\n".join(_text_blocks(result))
    ids = [int(value) for value in re.findall(r"(?m)^\s*(?:[-*]\s*)?(\d+)\s*:", listing)]
    if not ids:
        # Chrome DevTools MCP 1.8.0 has emitted both ordinal and prose list
        # formats. The shared parser is conservative; small values are the
        # only valid page ordinals, while URL/course numbers are discarded.
        ids = [value for value in page_id_candidates(result) if 0 < value <= 100]
    return list(dict.fromkeys(ids))


def _find_inventory(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if isinstance(value.get("activities"), list) and "generation" in value:
            return value
        for child in value.values():
            found = _find_inventory(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_inventory(child)
            if found is not None:
                return found
    elif isinstance(value, str):
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", value):
            try:
                decoded, _ = decoder.raw_decode(value[match.start() :])
            except json.JSONDecodeError:
                continue
            found = _find_inventory(decoded)
            if found is not None:
                return found
    return None


def _evaluation_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    structured = _find_inventory(result.get("structuredContent"))
    if structured is not None:
        return structured
    for text in _text_blocks(result):
        found = _find_inventory(text)
        if found is not None:
            return found
    return _find_inventory(result)


def _tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))


def _record(raw: dict[str, Any]) -> ActivityRecord:
    checks = raw.get("check_controls") if isinstance(raw.get("check_controls"), dict) else {}
    submits = raw.get("submit_controls") if isinstance(raw.get("submit_controls"), dict) else {}
    return ActivityRecord(
        activity_index=int(raw.get("activity_index", 0)),
        activity_id=str(raw.get("activity_id", "")),
        section=str(raw.get("section", "unknown")),
        participation_marker=bool(raw.get("participation_marker")),
        challenge_markers=_tuple(raw.get("challenge_markers")),
        lab_markers=_tuple(raw.get("lab_markers")),
        major_control_types=_tuple(raw.get("major_control_types")),
        aria_roles=_tuple(raw.get("aria_roles")),
        iframe_count=int(raw.get("iframes", 0)),
        native_draggable_count=int(raw.get("native_draggable", 0)),
        sortable_markers=_tuple(raw.get("sortable_markers")),
        term_bank_markers=_tuple(raw.get("term_bank_markers")),
        drag_drop_roles=_tuple(raw.get("drag_drop_roles")),
        pointer_markers=_tuple(raw.get("pointer_markers")),
        keyboard_reorder_markers=_tuple(raw.get("keyboard_reorder_markers")),
        canvas_count=int(raw.get("canvas", 0)),
        svg_count=int(raw.get("svg", 0)),
        completion_markers=_tuple(raw.get("completion_markers")),
        check_control_count=int(checks.get("count", 0)),
        enabled_check_control_count=int(checks.get("enabled", 0)),
        submit_control_count=int(submits.get("count", 0)),
        enabled_submit_control_count=int(submits.get("enabled", 0)),
        visible=bool(raw.get("visible")),
        fingerprint=str(raw.get("fingerprint", "")),
    )


def _inspect_page(
    process: subprocess.Popen[str],
    selector: selectors.BaseSelector,
    page_id: int,
    observer: str,
    request_id: int,
    timeout: float,
) -> tuple[dict[str, Any] | None, int, str]:
    selected, _ = tool_call(
        process,
        selector,
        request_id,
        "select_page",
        {"pageId": page_id, "bringToFront": False},
        timeout,
    )
    request_id += 1
    if not selected:
        return None, request_id, "select_failed"
    function = f"() => {{ {observer}\n return window.__zybooksDeterministicObserver.inspect({json.dumps({'targetId': str(page_id)})}); }}"
    evaluated, result = tool_call(
        process,
        selector,
        request_id,
        "evaluate_script",
        {
            "pageId": page_id,
            "function": function,
            "waitForStableDom": False,
        },
        timeout,
    )
    request_id += 1
    if not evaluated:
        return None, request_id, "evaluate_failed"
    payload = _evaluation_payload(result)
    return payload, request_id, "ok" if payload is not None else "payload_missing"


def _dispose_page(
    process: subprocess.Popen[str],
    selector: selectors.BaseSelector,
    page_id: int,
    request_id: int,
    timeout: float,
) -> tuple[bool, int]:
    selected, _ = tool_call(
        process,
        selector,
        request_id,
        "select_page",
        {"pageId": page_id, "bringToFront": False},
        timeout,
    )
    request_id += 1
    if not selected:
        return False, request_id
    disposed, _ = tool_call(
        process,
        selector,
        request_id,
        "evaluate_script",
        {
            "pageId": page_id,
            "function": "() => window.__zybooksDeterministicObserver ? window.__zybooksDeterministicObserver.dispose() : {disposed: false}",
            "waitForStableDom": False,
        },
        timeout,
    )
    return disposed, request_id + 1


def run(command: str, command_args: list[str], observer_path: Path, section: str, timeout: float) -> dict[str, Any]:
    process = subprocess.Popen(
        [command, *command_args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdin and process.stdout
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "hermes-zybooks-readonly-probe", "version": "1"},
        },
    }
    process.stdin.write(json.dumps(initialize) + "\n")
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
    process.stdin.flush()
    try:
        init = read_response(process, selector, 1, timeout)
        tools = read_response(process, selector, 2, timeout)
        if "result" not in init or "result" not in tools:
            raise RuntimeError("Chrome MCP initialization failed")
        listed, listing = tool_call(process, selector, 3, "list_pages", {}, timeout)
        if not listed:
            raise RuntimeError("Chrome MCP page listing failed")
        page_ids = _page_ids(listing)
        if not page_ids:
            raise RuntimeError("Chrome MCP page listing had no parseable page IDs")
        observer = observer_path.read_text(encoding="utf-8")
        pages: list[dict[str, Any]] = []
        probe_status: list[dict[str, Any]] = []
        request_id = 4
        for page_id in page_ids:
            inspection, request_id, status = _inspect_page(process, selector, page_id, observer, request_id, timeout)
            probe_status.append({"page_id": page_id, "status": status})
            if inspection is None:
                continue
            if inspection.get("section", {}).get("key") != section:
                probe_status[-1]["status"] = "wrong_section"
                continue
            pages.append({"target_id": str(page_id), "inspection": inspection})

        candidates = [
            TargetCandidate(
                target_id=page["target_id"],
                url=f"https://learn.zybooks.com{page['inspection'].get('path', '/')}",
                course=page["inspection"].get("title"),
                chapter=page["inspection"].get("section", {}).get("chapter"),
                section=page["inspection"].get("section", {}).get("section"),
                title=page["inspection"].get("title"),
                dom_section_heading=page["inspection"].get("section_heading"),
                page_fingerprint=page["inspection"].get("page_fingerprint", ""),
            )
            for page in pages
        ]
        selection = TargetSelector().select(candidates)
        inventories = []
        for page in pages:
            records = [_record(raw) for raw in page["inspection"].get("activities", []) if isinstance(raw, dict)]
            classified = ActivityClassifier().inventory(records)
            inventories.append({
                "target_id": page["target_id"],
                "generation": page["inspection"].get("generation"),
                "activities": [record.diagnostic() for record in classified],
                "custom_interaction_candidates": list(ActivityClassifier().custom_interaction_candidates(classified)),
            })
        disposed_count = 0
        for page in pages:
            disposed, request_id = _dispose_page(process, selector, int(page["target_id"]), request_id, timeout)
            disposed_count += int(disposed)
        cua = CuaPreflight().check(display=os.environ.get("DISPLAY"))
        return {
            "target_selection": selection.diagnostic(),
            "inventories": inventories,
            "probe_status": probe_status,
            "observer_instances_disposed": disposed_count,
            "cua_preflight": str(cua),
            "course_mutations": 0,
        }
    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-command", default="chrome-devtools-mcp")
    parser.add_argument("--observer", type=Path, required=True)
    parser.add_argument("--section", default="2.5")
    parser.add_argument("--timeout", type=float, default=30.0)
    parsed, command_args = parser.parse_known_args()
    if command_args and command_args[0] == "--":
        command_args = command_args[1:]
    print(json.dumps(run(parsed.mcp_command, command_args, parsed.observer, parsed.section, parsed.timeout), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
