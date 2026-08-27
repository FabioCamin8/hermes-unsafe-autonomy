# Research and design decisions

This document records the primary-source facts used to keep the implementation
narrow. It is a design record, not a promise that upstream behavior will stay
unchanged.

## Current-source snapshot

Primary-source metadata was refreshed on `2026-08-27`:

- Hermes latest public release: `v2026.8.27`, dereferenced tag commit
  `5fc308a70719a83cccdbba4c0e39c23f5a8239d5`.
- Chrome DevTools MCP latest public release and npm `latest`: `1.8.0`, release
  commit `45f187b1e3202c9f32ddba913be5d68751c3caa3`.
- Codex npm `latest` and latest public release: `0.150.1`.
- CUA main currently resolves to `90295148d34dac8e5a1307bac917e08171af5839`.

The VM retains Hermes `0.20.5` because that exact installation, including its
carried local commit, is the validated runtime; this task does not perform a
Hermes upgrade. The pinned Chrome MCP and Codex package versions remain the
current stable releases. Package engine metadata confirms Chrome MCP requires
Node `^20.19.0 || ^22.12.0 || >=23`; the VM has Node `20.19.2`. Codex's npm
package declares Node `>=16`.

## Hermes Agent

The live VM reports version `0.20.5`, upstream commit `c693c772`, with a local
carried commit. The corresponding public release research snapshot is
`v2026.8.19` at commit `a9611f3c6f7ff287a4f10f71a77d7c5a808ea1c8`. These IDs
are recorded separately because a local carried checkout and a public release
snapshot are not interchangeable:

- Approval modes are `manual`, `smart`, and `off`; hardline blocks and
  `approvals.deny` still run before YOLO/off.
- `execute_code` is arbitrary local Python and has a documented
  non-interactive auto-approval caveat; cron and single-query execution
  default to deny.
- MCP subprocess environments are filtered, with explicit user-environment
  overrides.

The inspected source establishes:

1. User memory providers are discovered under
   `$HERMES_HOME/plugins/<name>/` and selected by `memory.provider`.
2. Only one external memory provider is active at a time; native
   `MEMORY.md`/`USER.md` memory and session history remain separate.
3. `MemoryProvider.initialize()` receives the active `hermes_home`, so a
   provider must not hard-code `~/.hermes`.
4. The current pre-compression checkpoint contract is API version 2.
5. `compression.checkpoint_required=true` is fail-closed: compression must
   have an active compatible provider and a successful durable checkpoint.
6. The native MCP configuration is stored under `mcp_servers`; the CLI
   `mcp add` command is discovery-first and interactive, so unattended
   bootstrap uses the native dotted config writer for validated local stdio
   entries instead.

Relevant upstream source areas are `agent/memory_provider.py`,
`plugins/memory/__init__.py`, `agent/native_compaction.py`,
`agent/memory_manager.py`, and `hermes_cli/mcp_config.py`.

## Chrome DevTools MCP

The pinned package is `chrome-devtools-mcp@1.8.0` (release target commit
`45f187b1e3202c9f32ddba913be5d68751c3caa3`; source snapshot
`2dc104ce1bec57f17763cb7d72b33e03057a79bc`). The validated command-line
contract uses:

```text
--browser-url=http://127.0.0.1:9222
--no-usage-statistics
--no-performance-crux
```

The package requires Node LTS/20 or newer. Official support is Chrome and
Chrome for Testing; Debian Chromium is therefore accepted only after empirical
loopback CDP and MCP stdio validation. The package’s default CLI behavior
includes headless mode, unrestricted paths, and an isolated profile, so this
project explicitly supplies the browser URL and records that the existing
profile is outside Git/backups. The project does not expose CDP on a LAN
address.

## Codex MCP

The pinned package is `@openai/codex@0.150.1`, researched at commit
`6c59264b14b963d45d1005e7a8b1de87d4b054e2`. Hermes’ current preset and the
Codex CLI expose the experimental stdio command:

```text
codex mcp-server
```

The current source emits a deprecation warning, and approval requests use
`elicitation/create`; patch/exec approval failures deny conservatively. The
package can be installed and configured without an account login. Whether the
command can serve useful tools depends on external Codex authentication; that
is deliberately left to an operator and is recorded as a separate gate.

## CUA / graphical browser

The target has CUA driver `0.22.0` and Debian Chromium
`151.0.7922.169-1~deb13u1`. The research source snapshot is
`90295148d34dac8e5a1307bac917e08171af5839`. Its canonical permission modes
are `standard`, `bounded`, and `unrestricted`; existing-profile attachment
requires trusted host/launch authorization or an explicit unrestricted
acknowledgement. Grants are in-memory with idle/absolute TTLs and reconnect
invalidation. Existing-profile CDP is loopback-literal and allowlisted.

Linux X11 depends on `_NET_WM_PID`, native X11 ownership, `/proc` socket
ownership, and AT-SPI. Wayland generally cannot provide focus-free raw input.
The known open issues include Linux X11 AT-SPI/input-route failures (#3239),
Chrome 151 `Browser.getWindowForTarget` failures (#2704), English AT-SPI
consent coupling (#3137), the recommendation to use bounded unattended mode
(#2572), and exact browser/desktop acceptance matrices (#2284). These issues
are reasons to preserve the graphical proof boundary; they are not silently
treated as browser success.

## Chosen architecture

- SQLite is the structured source of truth because Hermes already runs local
  SQLite and the Python standard library supplies backup/integrity APIs.
- FTS5 keeps recall dependency-free and testable on the target’s Python.
- Markdown materialization gives an operator-readable view without making
  Markdown parsing the transactional index.
- JSONL is append-only evidence; incomplete tails are repaired before append.
- Redaction occurs before every durable representation, including provenance,
  metadata, journal, and materialized Markdown.
- Recovery copies databases with SQLite’s backup API and intentionally excludes
  browser profile state.

## Revalidation triggers

Repeat research and the full acceptance matrix when Hermes changes its
`MemoryProvider` or checkpoint API, when MCP package major versions change,
when Node/Chromium is upgraded, when the user gateway unit changes, or when an
external authentication model changes. Do not weaken a fail-closed check to
make a new version appear green.

## Source links

- [Hermes current release source](https://github.com/NousResearch/hermes-agent/tree/5fc308a70719a83cccdbba4c0e39c23f5a8239d5)
- [Hermes validated release source](https://github.com/NousResearch/hermes-agent/tree/a9611f3c6f7ff287a4f10f71a77d7c5a808ea1c8)
- [Hermes approval implementation](https://github.com/NousResearch/hermes-agent/blob/a9611f3c6f7ff287a4f10f71a77d7c5a808ea1c8/tools/approval.py)
- [Hermes release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.8.19)
- [Chrome DevTools MCP source](https://github.com/ChromeDevTools/chrome-devtools-mcp/tree/2dc104ce1bec57f17763cb7d72b33e03057a79bc)
- [Chrome DevTools MCP release](https://github.com/ChromeDevTools/chrome-devtools-mcp/releases/tag/chrome-devtools-mcp-v1.8.0)
- [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/)
- [OpenAI Codex MCP documentation](https://developers.openai.com/codex/mcp-server)
- [Codex MCP interface](https://github.com/openai/codex/blob/6c59264b14b963d45d1005e7a8b1de87d4b054e2/codex-rs/docs/codex_mcp_interface.md)
- [Codex release](https://github.com/openai/codex/releases/tag/rust-v0.150.1)
- [CUA source](https://github.com/trycua/cua/tree/90295148d34dac8e5a1307bac917e08171af5839)
- [CUA permission modes](https://github.com/trycua/cua/blob/90295148d34dac8e5a1307bac917e08171af5839/docs/content/docs/reference/cua-driver/permission-modes.mdx)
- [CUA browser attachment](https://github.com/trycua/cua/blob/90295148d34dac8e5a1307bac917e08171af5839/docs/content/docs/reference/cua-driver/browser-profile-attachment.mdx)
- [Debian Chromium metadata](https://packages.debian.org/trixie/chromium)
