#!/usr/bin/env python3
"""Small dependency-free MCP stdio initialize/tools-list probe."""

from __future__ import annotations

import argparse
import json
import re
import selectors
import subprocess
import sys
import time


def read_response(
    process: subprocess.Popen[str],
    selector: selectors.BaseSelector,
    request_id: int,
    timeout: float,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = selector.select(max(0.05, deadline - time.monotonic()))
        for key, _ in events:
            line = key.fileobj.readline()
            if not line:
                raise RuntimeError(f"MCP process exited before response id={request_id}")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                return message
    raise TimeoutError(f"timed out waiting for MCP response id={request_id}")


def send_request(
    process: subprocess.Popen[str],
    selector: selectors.BaseSelector,
    request_id: int,
    method: str,
    params: dict,
    timeout: float,
) -> dict:
    assert process.stdin
    process.stdin.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        + "\n"
    )
    process.stdin.flush()
    return read_response(process, selector, request_id, timeout)


def tool_call(
    process: subprocess.Popen[str],
    selector: selectors.BaseSelector,
    request_id: int,
    name: str,
    arguments: dict,
    timeout: float,
) -> tuple[bool, dict]:
    response = send_request(
        process,
        selector,
        request_id,
        "tools/call",
        {"name": name, "arguments": arguments},
        timeout,
    )
    result = response.get("result", {})
    if not isinstance(result, dict):
        return False, {}
    return (
        "result" in response and not bool(result.get("isError", False)),
        result,
    )


def page_id_candidates(result: dict) -> list[int]:
    """Extract bounded numeric page identifiers from MCP's text listing.

    Chrome DevTools MCP 1.8.0 returns ``list_pages`` as human-readable text,
    not a stable JSON object.  Keep the parser deliberately conservative and
    never print the listing because it can contain private titles and URLs.
    """

    text = "".join(
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )
    candidates: list[int] = []
    for token in re.findall(r"\d+", text):
        try:
            value = int(token)
        except ValueError:
            continue
        # JSON numbers must remain exactly representable in JavaScript.
        if value <= 9_007_199_254_740_991 and value not in candidates:
            candidates.append(value)
    return candidates


def browser_smoke(
    process: subprocess.Popen[str],
    selector: selectors.BaseSelector,
    timeout: float,
    request_id: int,
) -> tuple[dict, int]:
    """Exercise harmless page reads without exposing page content."""

    listed, listing = tool_call(
        process, selector, request_id, "list_pages", {}, timeout
    )
    request_id += 1
    candidates = page_id_candidates(listing) if listed else []
    selected = False
    for candidate in candidates:
        selected, _ = tool_call(
            process,
            selector,
            request_id,
            "select_page",
            {"pageId": candidate, "bringToFront": False},
            timeout,
        )
        request_id += 1
        if selected:
            break

    operations: dict[str, bool] = {}
    if selected:
        calls = (
            ("take_snapshot", {"pageId": candidate}),
            (
                "evaluate_script",
                {
                    "pageId": candidate,
                    "function": "() => ({title: document.title, url: location.href})",
                    "waitForStableDom": False,
                },
            ),
            (
                "list_console_messages",
                {"pageId": candidate, "pageSize": 5},
            ),
            (
                "list_network_requests",
                {"pageId": candidate, "pageSize": 5},
            ),
        )
        for name, arguments in calls:
            ok, _ = tool_call(
                process, selector, request_id, name, arguments, timeout
            )
            request_id += 1
            operations[name] = ok

    result = {
        "passed": listed and selected and all(operations.values()),
        "list_pages": listed,
        "page_candidates": len(candidates),
        "page_selected": selected,
        "operations": operations,
    }
    return result, request_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--exercise-browser", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parsed, command_args = parser.parse_known_args()
    parsed.args = [argument for argument in command_args if argument != "--"]
    process = subprocess.Popen(
        [parsed.command, *parsed.args],
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
            "clientInfo": {"name": "hermes-unsafe-autonomy-probe", "version": "1"},
        },
    }
    process.stdin.write(json.dumps(initialize) + "\n")
    process.stdin.write(
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        + "\n"
    )
    process.stdin.write(
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        + "\n"
    )
    process.stdin.flush()
    try:
        init_response = read_response(process, selector, 1, parsed.timeout)
        tools_response = read_response(process, selector, 2, parsed.timeout)
        result = tools_response.get("result", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
        output = {
            "initialized": "result" in init_response,
            "server": init_response.get("result", {}).get("serverInfo", {}),
            "tool_count": len(names),
            "tools": names,
        }
        browser_ok = True
        if parsed.exercise_browser:
            browser_result, _ = browser_smoke(
                process, selector, parsed.timeout, request_id=3
            )
            output["browser_smoke"] = browser_result
            browser_ok = bool(browser_result["passed"])
        print(
            json.dumps(output, sort_keys=True)
        )
        return (
            0
            if "result" in init_response
            and isinstance(tools, list)
            and browser_ok
            else 1
        )
    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(f"mcp_probe: {exc}", file=sys.stderr)
        raise SystemExit(1)
