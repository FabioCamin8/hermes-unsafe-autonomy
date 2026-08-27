# VM reboot recovery evidence

Status: PASS

This is a sanitized live acceptance record for the controlled VM reboot gate.
It contains only synthetic validation data and operational status; no runtime
database, raw session log, browser state, credential, cookie, token, or private
conversation content is published.

## Run metadata

- Date: 2026-08-27
- Pre-reboot capture: `2026-08-27T15:47:52Z`
- Reconnect capture: `2026-08-27T15:48:33Z`
- Target: the authorized dedicated graphical Debian VM; the same hostname,
  machine identity, and KVM guest were confirmed after reconnect.
- Mechanism: clean guest-initiated `sudo -n systemctl reboot` over the existing
  authorized SSH path; the Proxmox host and unrelated guests were not rebooted.
- Pre-reboot boot time: `2026-08-27 09:36:01`
- Post-reboot boot time: `2026-08-27 15:48:23`
- Boot identity: `pre_boot_id != post_boot_id`
- Real reboot confirmed: PASS; the boot IDs differed in the private run.
- Reconnect: PASS on the second bounded SSH attempt; the expected temporary
  disconnect occurred.

## Pre-reboot marker and fixed probe

Session A created the marker through the normal Hermes durable-memory upsert
path and exited successfully before the reboot:

- Synthetic key: `validation.reboot.marker`
- Synthetic value: `validation-reboot-synthetic-value`
- Session A: `session-A`
- Durable memory ID: `synthetic-record-id`
- Active state: `true`
- Source type: `user_explicit`
- Trust/confidence: `high` / `1.0`
- Pre-reboot lookup: PASS; the provider returned the active record and
  metadata, and `hermes-vault integrity --json` returned SQLite `ok`.

The exact post-reboot prompt was fixed and recorded before reboot. The
identifiers and value below are generalized publication-safe placeholders:

```text
From the context provided to you at turn start, copy character-for-character the token after the words Synthetic reboot validation value for the record keyed validation.reboot.marker. Answer only from the context provided at turn start; do not perform any tool call. Reply with exactly value=<token> and nothing else.
```

The prompt does not request a vault search, a memory lookup, SQLite, or any
other explicit tool call.

## Recovery results

| Gate | Result | Evidence |
| --- | --- | --- |
| OS/network/home | PASS | Debian 13 returned normally; `hermes` exists, home ownership/mode remained `hermes:hermes`/`0700`, default route and DNS resolved, and no failed system or user units were reported. |
| Hermes gateway auto-start | PASS | `hermes-gateway.service` was active and enabled at reconnect, entered active state at `15:48:29 UTC`, and its main process was owned by `hermes`; no manual gateway start was performed. |
| Unsafe sudo persistence | PASS | `sudo -n true`, `sudo -n id -u`, `sudo -n -u hermes sudo -n id -u`, and the dedicated `visudo` check passed with UID `0`. |
| Vault provider recovery | PASS | `hermes_vault` remained discovered/active; vault mode was `0700`, database mode `0600`, schema version `1`, and pending writes were `0`. |
| SQLite/FTS | PASS | Provider integrity returned `{"ok": true, "sqlite": "ok"}`; read-only schema inspection confirmed `memory_fts` and `journal_fts` structures. |
| Durable marker persistence | PASS | Post-reboot lookup found the same ID, key, value, active state, `user_explicit` provenance, high trust, confidence `1.0`, and pre-reboot creation time. |
| Automatic post-reboot recall | PASS | Fresh Session B `session-B` emitted `Hermes Vault — recalled 1 memory` and returned `validation-reboot-synthetic-value` exactly; it was distinct from Session A and used the fixed indirect prompt. |
| Chromium recovery | PASS | Chromium was already running under `hermes` after the graphical session returned; version was `151.0.7922.169`; no manual browser start was performed. |
| CDP loopback-only | PASS | The only port-9222 listener was `127.0.0.1:9222`; the version endpoint responded, and no project tunnel/reverse-proxy candidate was present. |
| Chrome DevTools MCP | PASS | Pinned server `1.8.0`, Node `v26.7.0`, and Chromium `151.0.7922.169`; initialization listed 29 tools and the harmless page-list/select/snapshot/title-URL/console/network probe passed. |
| CUA recovery | PASS | Graphical `hermes-graphical hermes computer-use doctor` returned `cua-driver 0.22.0` `ok`; MCP session, X11, AT-SPI, screen capture, and X11 input capabilities passed. |
| Codex MCP initialization | PASS | Pinned Codex `0.150.1` initialized and listed two tools; no OAuth was started. |
| Codex specialist execution | BLOCKED_AUTH | External Codex authentication remains absent; this is an intentional non-blocking authorization boundary. |
| Native session search | PASS | `hermes sessions list --source validation --limit 5` returned successfully after reboot. |
| Aggregate health | PASS | Final `hermes-health` returned `Overall: PASS` with gateway, provider, vault, integrity, CDP, Chrome MCP, Codex MCP, CUA, unsafe-root, and loopback exposure all passing. |

Post-cleanup Codex probing showed intermittent startup behavior: two aggregate
health invocations reported `Codex MCP: FAIL` / `Overall: FAIL`, and one direct
120-second probe timed out waiting for `tools/list`. The marker cleanup and
SQLite checks had already passed. The intervening direct Codex probe and the
final serialized aggregate-health rerun passed without any service,
configuration, authentication, or implementation change. The post-reboot
protocol initialization and final health result above are the authoritative
passing gates; the intermittent probe behavior is retained as a limitation.

## Local validation and cleanup

- Syntax: `bash -n bootstrap.sh scripts/*.sh` — PASS.
- Automated suite: `17 passed, 0 failed, 0 skipped`.
- Focused fail-closed checkpoint test: `1 passed, 0 failed`.
- Public-tree scan: `PUBLIC_TREE_SCAN=PASS`.
- Cleanup: supported soft forget returned `True`; active search returned `[]`;
  deleted count increased from `11` to `12`; final integrity remained
  `{"ok": true, "sqlite": "ok"}`.
- No runtime database, private audit log, browser profile, or credential was
  added to the repository.

## Final verdict

PASS

The previously outstanding VM reboot recovery gate is now PASS. The Hermes
unsafe-autonomy runtime Definition of Done is complete. Codex authenticated
specialist execution remains `BLOCKED_AUTH` and is non-blocking.
