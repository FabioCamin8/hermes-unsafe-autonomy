"""Small dependency-free CDP reader for sanitized, read-only inspection."""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .controller import TargetCandidate, TargetEvidence
from .reconciliation import TargetReconciler, TargetReconciliation


class CdpError(RuntimeError):
    pass


def _safe_path(url: str) -> str:
    match = re.search(r"/chapter/([^/]+)/section/([^/?#]+)", url)
    if match:
        return f"/chapter/{match.group(1)[:80]}/section/{match.group(2)[:80]}"
    return urlsplit(url).path[:120] or "/"


def _loopback_endpoint(endpoint: str) -> tuple[str, str, int]:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise CdpError("CDP endpoint must be loopback HTTP(S)")
    return parsed.scheme, parsed.hostname, parsed.port or 80


def _http_json(endpoint: str, path: str) -> dict[str, Any] | list[Any]:
    _loopback_endpoint(endpoint)
    with urllib.request.urlopen(endpoint.rstrip("/") + path, timeout=5) as response:
        value = json.load(response)
    if not isinstance(value, (dict, list)):
        raise CdpError("CDP endpoint returned a non-JSON object")
    return value


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise CdpError("CDP websocket closed")
        data.extend(chunk)
    return bytes(data)


class _WebSocket:
    def __init__(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise CdpError("CDP websocket must be loopback ws://")
        self.sock = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {parsed.path or '/'}{('?' + parsed.query) if parsed.query else ''} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port or 80}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self.sock.sendall(request)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            response.extend(self.sock.recv(4096))
        if not response.startswith(b"HTTP/1.1 101"):
            self.close()
            raise CdpError("CDP websocket handshake failed")
        self._next_id = 0

    def _send(self, payload: bytes) -> None:
        size = len(payload)
        frame = bytearray([0x81])
        if size < 126:
            frame.append(0x80 | size)
        elif size < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack("!H", size))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack("!Q", size))
        mask = os.urandom(4)
        frame.extend(mask)
        frame.extend(bytes(value ^ mask[index % 4] for index, value in enumerate(payload)))
        self.sock.sendall(frame)

    def _receive(self) -> tuple[int, bytes]:
        first, second = _read_exact(self.sock, 2)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", _read_exact(self.sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _read_exact(self.sock, 8))[0]
        mask = _read_exact(self.sock, 4) if masked else b""
        data = _read_exact(self.sock, length)
        if masked:
            data = bytes(value ^ mask[index % 4] for index, value in enumerate(data))
        return opcode, data

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        self._send(json.dumps({"id": request_id, "method": method, "params": params or {}}).encode("utf-8"))
        while True:
            opcode, data = self._receive()
            if opcode == 0x8:
                raise CdpError("CDP websocket closed")
            if opcode == 0x9:
                continue
            if opcode != 0x1:
                continue
            message = json.loads(data.decode("utf-8"))
            if message.get("id") == request_id:
                if "error" in message:
                    raise CdpError(f"CDP {method} failed")
                return message.get("result", {})

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


@dataclass(frozen=True)
class TargetInspection:
    target: dict[str, Any]
    candidate: TargetCandidate | None


def _page_expression() -> str:
    return r"""(() => {
      const fnv = (input) => {
        let value = 2166136261;
        for (let index = 0; index < input.length; index += 1) {
          value ^= input.charCodeAt(index);
          value = Math.imul(value, 16777619);
        }
        return (value >>> 0).toString(16).padStart(8, "0");
      };
      const sectionMatch = location.pathname.match(/\/chapter\/([^/]+)\/section\/([^/]+)/i);
      const roots = Array.from(document.querySelectorAll(".interactive-activity-container"));
      const tokens = (node) => Array.from(node.classList || []).sort().join(".").slice(0, 160);
      const attrs = (node) => Array.from(node.attributes || []).map((item) => item.name)
        .filter((name) => name !== "value" && name !== "title" && !name.startsWith("aria-label"))
        .sort().join(",");
      const fingerprint = (root) => fnv([root, ...Array.from(root.querySelectorAll("*"))].slice(0, 500)
        .map((node) => [node.tagName.toLowerCase(), tokens(node), node.getAttribute("role") || "",
          node.getAttribute("type") || "", node.children.length, attrs(node)].join("|"))
        .join(String.fromCharCode(10)));
      const heading = Array.from(document.querySelectorAll("h1,h2,h3"))
        .map((node) => (node.textContent || "").replace(/\s+/g, " ").trim())
        .find((value) => value && /section|chapter|\d+\.\d+/i.test(value));
      const inspect = (root, index) => {
        const all = [root, ...Array.from(root.querySelectorAll("*"))].slice(0, 500);
        const source = all.map((node) => `${node.id || ""} ${tokens(node)} ${attrs(node)}`).join(" ").toLowerCase();
        const roles = Array.from(root.querySelectorAll("[role]"))
          .map((node) => node.getAttribute("role")).filter(Boolean);
        const sortable = /sortable|drag-item|drop-target|droppable|drop-zone|matching/.test(source);
        const termBank = /term-bank|termbank/.test(source);
        const pointer = root.querySelector("[draggable='true'],[draggable=true],[onpointerdown],[ontouchstart],[onmousedown],[style*='cursor: grab']") !== null;
        const keyboard = root.querySelector("[aria-grabbed],[aria-dropeffect],[aria-keyshortcuts],[onkeydown],[onkeyup]") !== null
          || (/reorder|sortable|drag|drop|matching/.test(source) && root.querySelector("[role='option'],[tabindex]") !== null);
        const signals = [];
        if (root.querySelectorAll("[draggable='true'],[draggable=true]").length) signals.push("native_draggable");
        if (sortable) signals.push("sortable_or_drop_zone");
        if (termBank) signals.push("term_bank");
        for (const role of roles.filter((role) => /drag|drop|option/i.test(role))) signals.push(`role:${role}`);
        if (pointer) signals.push("pointer_handler");
        if (keyboard) signals.push("keyboard_reorder_semantics");
        if (root.querySelectorAll("canvas").length) signals.push("canvas");
        if (root.querySelectorAll("svg").length && (pointer || keyboard)) signals.push("svg_gesture");
        return {
          index: index + 1,
          activity_id: (root.getAttribute("data-activity-id") || root.getAttribute("data-activity") || root.id || ((sectionMatch ? sectionMatch[1] + "." + sectionMatch[2] : "unknown") + "." + (index + 1))).slice(0, 80),
          fingerprint: fingerprint(root),
          visible: getComputedStyle(root).display !== "none" && getComputedStyle(root).visibility !== "hidden" && root.getClientRects().length > 0,
          completion_marker_count: all.filter((node) => Array.from(node.classList || []).some((token) => /^(complete|completed|correct|success|finished|done)$/i.test(token))
            || ["data-complete","data-completed","data-correct","aria-complete"].some((name) => node.hasAttribute(name))).length,
          native_draggable_count: root.querySelectorAll("[draggable='true'],[draggable=true]").length,
          sortable_signal: sortable,
          term_bank_signal: termBank,
          gesture_role_count: roles.filter((role) => /drag|drop|option/i.test(role)).length,
          pointer_signal: pointer,
          keyboard_reorder_signal: keyboard,
          canvas_count: root.querySelectorAll("canvas").length,
          svg_count: root.querySelectorAll("svg").length,
          iframe_count: root.querySelectorAll("iframe").length,
          custom_interaction_candidate: signals.length > 0,
          custom_interaction_signals: Array.from(new Set(signals)),
        };
      };
      const activity_fingerprints = roots.map(inspect);
      return {
        ready_state: document.readyState,
        visibility_state: document.visibilityState,
        has_focus: document.hasFocus(),
        prerendering: document.prerendering === true,
        was_discarded: document.wasDiscarded === true,
        performance_time_origin_ms: Math.round(performance.timeOrigin),
        performance_now_ms: Math.round(performance.now()),
        section_heading: heading ? heading.slice(0, 160) : null,
        section: sectionMatch ? { chapter: sectionMatch[1].slice(0, 40), section: sectionMatch[2].slice(0, 40), key: sectionMatch[1] + "." + sectionMatch[2] } : null,
        page_fingerprint: fnv([location.pathname, document.title, sectionMatch ? sectionMatch[1] + "." + sectionMatch[2] : "unknown", ...activity_fingerprints.map((item) => item.fingerprint)].join(String.fromCharCode(10))),
        activity_count: roots.length,
        activities: activity_fingerprints,
      };
    })()"""


class CdpTargetCollector:
    """Collect current browser target topology without selecting or foregrounding."""

    def __init__(self, endpoint: str = "http://127.0.0.1:9222") -> None:
        self.endpoint = endpoint.rstrip("/")
        version = _http_json(self.endpoint, "/json/version")
        listing = _http_json(self.endpoint, "/json/list")
        if not isinstance(version, dict) or not isinstance(listing, list):
            raise CdpError("CDP metadata shape is invalid")
        websocket_url = version.get("webSocketDebuggerUrl")
        if not isinstance(websocket_url, str):
            raise CdpError("CDP browser websocket is unavailable")
        endpoint_parts = urlsplit(self.endpoint)

        def local_websocket_url(value: str) -> str:
            parsed = urlsplit(value)
            port = endpoint_parts.port or 80
            netloc = f"[{endpoint_parts.hostname}]:{port}" if ":" in str(endpoint_parts.hostname) else f"{endpoint_parts.hostname}:{port}"
            return urlunsplit(("ws", netloc, parsed.path, parsed.query, ""))

        self.version = version
        self.listing = {}
        for row in listing:
            if isinstance(row, dict) and row.get("id"):
                copied = dict(row)
                if isinstance(copied.get("webSocketDebuggerUrl"), str):
                    copied["webSocketDebuggerUrl"] = local_websocket_url(copied["webSocketDebuggerUrl"])
                self.listing[str(row["id"])] = copied
        self.browser = _WebSocket(local_websocket_url(websocket_url))

    def close(self) -> None:
        self.browser.close()

    def _page_inspection(self, target_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        listing = self.listing.get(target_id)
        websocket_url = listing.get("webSocketDebuggerUrl") if listing else None
        if not isinstance(websocket_url, str):
            raise CdpError("page websocket is unavailable")
        page = _WebSocket(websocket_url)
        try:
            history = page.request("Page.getNavigationHistory")
            tree = page.request("Page.getFrameTree")
            evaluated = page.request("Runtime.evaluate", {"expression": _page_expression(), "returnByValue": True})
        finally:
            page.close()
        value = evaluated.get("result", {}).get("value")
        if not isinstance(value, dict):
            raise CdpError("page document probe returned no structured value")
        entries = history.get("entries", [])
        current_index = history.get("currentIndex")
        current_entry_id = None
        if isinstance(entries, list) and isinstance(current_index, int) and 0 <= current_index < len(entries):
            current_entry_id = entries[current_index].get("id")
        root_frame = tree.get("frameTree", {}).get("frame", {})
        navigation = {
            "current_index": current_index,
            "entry_count": len(entries) if isinstance(entries, list) else None,
            "current_entry_id": current_entry_id,
        }
        frame = {
            "frame_id": root_frame.get("id"),
            "loader_id": root_frame.get("loaderId"),
            "url_path": _safe_path(str(root_frame.get("url", ""))),
        }
        return navigation, {"frame": frame, "document": value}

    def collect(self, section: str) -> tuple[dict[str, Any], TargetReconciliation]:
        target_result = self.browser.request("Target.getTargets")
        infos = target_result.get("targetInfos", [])
        if not isinstance(infos, list):
            raise CdpError("Target.getTargets returned no target list")
        inspected: list[TargetInspection] = []
        for raw in infos:
            if not isinstance(raw, dict):
                continue
            target_id = str(raw.get("targetId", ""))
            item: dict[str, Any] = {
                "target_id": target_id,
                "target_type": raw.get("type"),
                "subtype": raw.get("subtype"),
                "title": str(raw.get("title", ""))[:160] if raw.get("type") == "page" else str(raw.get("type", "")),
                "path": _safe_path(str(raw.get("url", ""))),
                "browser_context_id": raw.get("browserContextId"),
                "opener_target_id": raw.get("openerId"),
                "opener_frame_id": raw.get("openerFrameId"),
                "parent_frame_id": raw.get("parentFrameId"),
                "parent_target_id": self.listing.get(target_id, {}).get("parentId"),
                "attached": raw.get("attached"),
                "cdp_reachable": False,
            }
            candidate = None
            if raw.get("type") == "page":
                try:
                    window = self.browser.request("Browser.getWindowForTarget", {"targetId": target_id})
                    item["window"] = window
                    navigation, page_data = self._page_inspection(target_id)
                    item["navigation"] = navigation
                    item.update(page_data)
                    item["cdp_reachable"] = True
                    document = page_data["document"]
                    page_section = document.get("section") if isinstance(document.get("section"), dict) else {}
                    if page_section.get("key") == section:
                        frame = page_data["frame"]
                        loader_id = frame.get("loader_id")
                        time_origin = document.get("performance_time_origin_ms")
                        generation = f"loader:{str(loader_id)[:16]}" if loader_id else f"time-origin:{time_origin}"
                        evidence = TargetEvidence(
                            target_type="page",
                            browser_context_id=raw.get("browserContextId"),
                            opener_target_id=raw.get("openerId"),
                            opener_frame_id=raw.get("openerFrameId"),
                            parent_frame_id=raw.get("parentFrameId"),
                            attached=raw.get("attached"),
                            window_id=window.get("windowId"),
                            window_state=window.get("bounds", {}).get("windowState"),
                            target_present=True,
                            cdp_reachable=True,
                            navigation_entry_count=navigation.get("entry_count"),
                            current_navigation_entry_id=navigation.get("current_entry_id"),
                            frame_id=frame.get("frame_id"),
                            loader_id=loader_id,
                            ready_state=document.get("ready_state"),
                            visibility_state=document.get("visibility_state"),
                            has_focus=document.get("has_focus"),
                            prerendering=document.get("prerendering"),
                            was_discarded=document.get("was_discarded"),
                            performance_time_origin_ms=time_origin,
                            performance_now_ms=document.get("performance_now_ms"),
                            section_heading=document.get("section_heading"),
                            activity_count=document.get("activity_count"),
                            document_generation=generation,
                        )
                        candidate = TargetCandidate(
                            target_id=target_id,
                            url=str(raw.get("url", "")),
                            section=page_section.get("key"),
                            title=str(raw.get("title", ""))[:160],
                            dom_section_heading=document.get("section_heading"),
                            page_fingerprint=str(document.get("page_fingerprint", "")),
                            target_type="page",
                            evidence=evidence,
                        )
                except Exception as exc:
                    item["probe_error"] = type(exc).__name__
            inspected.append(TargetInspection(item, candidate))

        candidates = tuple(item.candidate for item in inspected if item.candidate is not None)
        reconciliation = TargetReconciler().reconcile(candidates)
        candidate_ids = {candidate.target_id for candidate in candidates}
        for item in inspected:
            item.target["relevant"] = item.candidate is not None or item.target.get("parent_target_id") in candidate_ids
        report = {
            "browser": self.version.get("Browser"),
            "protocol_version": self.version.get("Protocol-Version"),
            "target_count": len(inspected),
            "targets": [item.target for item in inspected],
            "target_reconciliation": reconciliation.diagnostic(),
            "course_mutations": 0,
        }
        return report, reconciliation

    def close_target(self, target_id: str) -> bool:
        result = self.browser.request("Target.closeTarget", {"targetId": target_id})
        return bool(result.get("success"))
