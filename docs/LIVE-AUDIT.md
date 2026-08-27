# Live audit record

This file is a publishable, secret-safe record of acceptance. It intentionally
omits IP addresses, API-key fragments, browser profile names, message content,
and authenticated account details.

## Run metadata

- Date: 2026-08-27
- Target: authorized dedicated graphical Debian VM
- Hermes boundary: user systemd gateway owned by `hermes`
- Repository scope: local source and private VM only; no GitHub push
- Browser profile: excluded from repository and project backups
- Runtime acceptance: PASS; VM reboot recovery verified
- Source/publication readiness: READY

## Evidence summary

| Gate | Status | Safe evidence |
| --- | --- | --- |
| Pre-change backup | PASS | Private pre-autonomy and rehearsal backups used SQLite backup API; integrity checks passed. |
| Local provider tests | PASS | 17 tests passed, 0 failed, 0 skipped. |
| Public-tree policy | PASS | Final local public-tree scan passed; no GitHub push. |
| Bootstrap | PASS | Final bootstrap passed with pinned Chrome MCP `1.8.0`, Codex `0.150.1`, and default Codex protocol probe pass. |
| User/root boundary | PASS | `hermes` passwordless root check returned `0`; gateway remained an enabled user unit under `hermes`. |
| Vault discovery/recall | PASS | Live synthetic Session A/B acceptance recalled one exact marker automatically across distinct processes; sanitized evidence is preserved in `docs/evidence/cross-session-recall.md`. Supersession, provenance, redaction, untrusted clamping, checkpoint filtering/idempotency, and soft-forget remain covered by the local/provider checks. |
| Chrome DevTools MCP | PASS | CDP listened only on loopback; stdio initialization listed 29 tools; harmless page selection, snapshot, pure title/URL evaluation, console listing, and network listing all returned successfully without foregrounding or navigation. |
| Graphical CUA | PASS | Graphical wrapper health was `ok`; screenshot/state capture and matching Chromium `bring_to_front` passed. |
| Codex MCP protocol | PASS | Stdio initialization passed and listed two tools; no OAuth flow was invoked. |
| Codex specialist execution | BLOCKED_AUTH | External authentication was absent; no OAuth flow was invoked. |
| Native session search | PASS | Hermes native `sessions browse` search-capable picker was available and `sessions list` returned existing history. |
| Aggregate health | PASS | `hermes-health` covers gateway, provider, vault integrity, pending writes, durable timestamps, CDP exposure, Chrome MCP, Codex MCP, CUA, and unsafe-root state; graphical CUA doctor returned `ok`. |
| Backup/rollback | PASS | Selected private live-state rehearsal restored the runtime tree and launcher symlinks, removed the marker, preserved SQLite integrity/service state, and excluded the browser profile. |
| Pre-compression checkpoint failure | PASS | Focused test `test_required_checkpoint_failure_blocks_lossy_compression` and live installed `compress_context()` smoke inject a deterministic v2 checkpoint failure; the failure is visible, the lossy callback is not reached, and the original transcript remains unchanged. See `docs/evidence/checkpoint-failure.md`. |
| VM reboot recovery | PASS | Guest-initiated clean reboot changed the boot ID; gateway, vault, marker, automatic recall, Chromium/CDP, Chrome MCP, CUA, Codex MCP protocol, session search, health, cleanup, and local gates passed. See `docs/evidence/vm-reboot-recovery.md`. |

## Required private evidence commands

Run as `hermes` unless a command is explicitly marked root:

```bash
hermes-vault status --json
hermes-vault integrity --json
hermes memory status
hermes mcp list
hermes-health
scripts/validate-chrome-mcp.sh
hermes-mcp-probe "$HOME/.local/bin/codex" --timeout 60 -- mcp-server
```

All statuses above are based on reviewed private evidence. External
authentication remains operator-controlled and is not performed by bootstrap.
The controlled VM reboot gate is now `PASS`; the detailed sanitized evidence is
preserved in `docs/evidence/vm-reboot-recovery.md`. Codex probing showed
intermittent startup behavior during post-cleanup verification: aggregate
health reported a Codex failure twice and one direct probe timed out waiting
for `tools/list`, while the intervening direct probe and the final serialized
aggregate-health rerun passed without a runtime change. This is recorded as a
transient observation; the post-reboot protocol initialization and final health
gate are both passing.

## Explicit non-claims

This record does not claim that Codex is authenticated, that a model made a
safe autonomous decision, or that browser/profile rollback has been exercised
on the production profile. Those are separate acceptance boundaries.
