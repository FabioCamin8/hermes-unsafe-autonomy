# Validation contract

Validation is split into local source proof and live VM proof. A green local
test suite cannot prove a graphical browser, a user systemd bus, or external
Codex authentication.

## Local proof

From the repository root:

```bash
bash -n bootstrap.sh scripts/*.sh
python3 -m unittest discover -s tests -v
scripts/validate-public-tree.sh
scripts/validate-public-history.sh
```

The unit tests cover schema migration/integrity, FTS ranking, supersession,
provenance, trust clamping, redaction in every materialization, journaling,
checkpoint idempotency/filtering, forgetting, lifecycle hooks, and structured
provider errors. The current suite has 19 tests.

### Fail-closed checkpoint test

Run the focused regression:

```bash
python3 -m unittest \
  tests.test_provider.ProviderContractTests.test_required_checkpoint_failure_blocks_lossy_compression
```

Expected result is one passing test. The test injects a deterministic failure
into the real `VaultStore.checkpoint()` call used by
`HermesVaultProvider.on_pre_compress()`. It asserts that the v2 checkpoint path
was invoked, the exception is visible to the caller, the lossy compression
callback is never reached, and the original transcript is unchanged. This is
the public provider/compression boundary test; the installed Hermes core's
`MemoryManager` and `compress_context()` are external to this repository. The
live installed-core smoke additionally exercises that path with
`compression.checkpoint_required=true`; its sanitized result is preserved in
[checkpoint-failure evidence](evidence/checkpoint-failure.md).

## Live acceptance matrix

| Gate | Evidence | Required result |
| --- | --- | --- |
| Correct target | hostname, OS, `id hermes` | Dedicated graphical VM; expected `hermes` account. |
| Privilege boundary | `sudo -n -u hermes sudo -n id -u`; user unit/process inspection | Prints `0`; gateway is user-scoped and not a system service. |
| Provider discovery | `hermes memory status`, gateway log/status | `hermes_vault` is active and available. |
| Cross-session recall | Upsert in one fresh session, end it, then query from a new process/session without naming a vault tool | Exact durable content is recalled automatically with a deterministic provider indicator; retain [sanitized evidence](evidence/cross-session-recall.md). |
| Supersession | Two same-key records plus `vault_search` | New record is active; old record remains historical/superseded. |
| Secret defense | Synthetic token through upsert, journal, Markdown, and JSONL scan | Synthetic value is absent from every materialization. |
| Compression gate | Focused local provider-boundary test plus live installed `compress_context()` failure smoke | Checkpoint is durable; required compression fails closed on missing/failed v2 support and leaves the transcript uncompressed. |
| Gateway restart | `systemctl --user restart hermes-gateway.service` then `hermes status` | User gateway returns active with provider loaded. |
| Chrome MCP | `hermes-mcp-probe "$HOME/.local/bin/chrome-devtools-mcp" --exercise-browser -- ...` | CDP is reachable only on loopback; MCP initializes, lists tools, selects an existing page without foregrounding, returns a structured snapshot, evaluates title/URL, and lists console/network records. |
| CUA | Graphical `hermes-graphical` session and one bounded UI probe | Proved only in the graphical session; SSH-only result is not sufficient. |
| Aggregate health | `hermes-health` | Reports gateway, provider, vault, integrity, pending writes, durable timestamps, CDP/MCP, CUA, root mode, and CDP exposure without credentials. |
| Codex MCP protocol | `hermes-mcp-probe "$HOME/.local/bin/codex" --timeout 60 -- mcp-server` | Initialize/tool-list pass. |
| Codex specialist execution | Same probe's authentication boundary | `BLOCKED_AUTH` when external credentials are absent; never start OAuth during validation. |
| Native session search | Hermes native session-search command | Existing session history remains available. |
| Backup/restore | New private backup, harmless marker, rollback on disposable copy or selected profile | SQLite integrity and service state are preserved; browser profile is untouched. |
| Final publication scan | `scripts/validate-public-tree.sh` and `git status` | No credentials, databases, sessions, JSONL, or browser profile are tracked. |

## Validator-reproducible commands

Run the local commands from the repository root. Run the live commands as
`hermes` inside the dedicated VM.

### Repository identity, tests, and health

```bash
git rev-parse HEAD
git rev-parse HEAD^{tree}
git status --short
python3 -m unittest discover -s tests -v
scripts/validate-public-tree.sh
```

The live health command is secret-free but refreshes private health metadata:

```bash
hermes-health
```

Required health result before the reboot gate is `Overall: PASS`; a Codex
credential absence is recorded separately as `BLOCKED_AUTH` only when the
Codex probe reports an authentication boundary, not silently relabeled as a
pass.

### Cross-session recall

Use only a new synthetic value. The following procedure keeps raw model output
in a temporary file and prints only safe status lines:

```bash
key=validation.cross_session.marker
value="validation-$(tr -d '-' </proc/sys/kernel/random/uuid)"
a_log=$(mktemp /tmp/hermes-cross-session-a.XXXXXX)
b_log=
trap 'rm -f -- "${a_log:-}" "${b_log:-}"' EXIT
(hermes chat -q "Use the active durable memory provider to create one active durable memory. Do not use terminal, file, browser, or MCP tools. Use the memory provider upsert tool with exactly these fields: kind=observation; canonical_key=$key; title=Synthetic cross-session validation marker; content=Synthetic validation value $value; source_type=user_explicit; trust=high; confidence=1.0. Once the upsert succeeds, reply exactly STORED. If it fails, reply exactly FAILED." \
  -Q --source validation --in /tmp --max-turns 8 --run-budget 100 -t memory >"$a_log" 2>&1) &
a_pid=$!
wait "$a_pid"; a_rc=$?
sed -n 's/^session_id:[[:space:]]*//p; /^STORED$/p' "$a_log" | tr -d '\r'
rm -f -- "$a_log"; a_log=
b_log=$(mktemp /tmp/hermes-cross-session-b.XXXXXX)

hermes-vault search "$key" --limit 8
record_id=$(PYTHONPATH="$HOME/.hermes/plugins" python3 - "$key" <<'PY'
import sys
from pathlib import Path
from hermes_vault.store import VaultStore

with VaultStore(Path.home() / ".hermes" / "vault") as store:
    rows = store.search(sys.argv[1], limit=8)
    print(rows[0]["id"] if rows else "")
PY
)

(hermes chat -q "From the context provided to you at turn start, copy character-for-character the token after the words Synthetic validation value for the record keyed validation.cross_session.marker. Do not call any explicit memory, vault, terminal, file, browser, or MCP tool. Reply with exactly value=<token> and nothing else." \
  -v --source validation --in /tmp --max-turns 4 --run-budget 100 -t memory >"$b_log" 2>&1) &
b_pid=$!
wait "$b_pid"; b_rc=$?
rg 'Hermes Vault — recalled|^value=|^Session:' "$b_log" | tr -d '\r'
returned=$(sed -n 's/^value=//p' "$b_log" | tr -d '\r' | tail -n1)
[[ "$a_rc" -eq 0 && "$b_rc" -eq 0 && -n "$record_id" && "$returned" == "$value" ]]
```

Record `a_pid`, `b_pid`, `a_rc`, and `b_rc`; Session A must have exited before
starting B. Record both Hermes session IDs, the
`Hermes Vault — recalled N memory` indicator, and an exact value comparison.
Soft-forget the returned record ID through the provider's supported API:

```bash
PYTHONPATH="$HOME/.hermes/plugins" python3 - "$record_id" <<'PY'
import sys
from pathlib import Path
from hermes_vault.store import VaultStore

with VaultStore(Path.home() / ".hermes" / "vault") as store:
    print(store.forget(sys.argv[1], hard_delete=False))
PY
```

Verify `hermes-vault search "$key" --limit 8` returns `[]`, and run
`hermes-vault integrity --json`. Do not commit the runtime database or raw
logs. See the preserved [sanitized evidence](evidence/cross-session-recall.md).

### CDP and MCP

```bash
ss -ltnH | awk '$4 ~ /:9222$/ {print $4}'
curl -fsS http://127.0.0.1:9222/json/version >/dev/null
hermes-mcp-probe "$HOME/.local/bin/chrome-devtools-mcp" \
  --exercise-browser -- \
  --browser-url=http://127.0.0.1:9222 \
  --no-usage-statistics --no-performance-crux
hermes-mcp-probe "$HOME/.local/bin/codex" --timeout 60 -- mcp-server
```

The listener check must show only `127.0.0.1:9222`; the Chrome probe must pass
its harmless page-list/select/snapshot/title/network/console behavior smoke.
Codex initialization may instead be `BLOCKED_AUTH` when the external account
credential is absent. Never start OAuth as part of validation.

## Controlled VM reboot acceptance (executed 2026-08-27; PASS)

The controlled guest reboot procedure below was executed against the authorized
dedicated graphical VM. The sanitized result is preserved in
[VM reboot recovery evidence](evidence/vm-reboot-recovery.md). It confirmed a
changed VM boot identity, automatic Hermes/gateway/vault/browser recovery,
automatic durable-memory recall, the loopback CDP boundary, graphical CUA,
MCP initialization, session search, health, cleanup, and the local test gates.
For a future rerun, do not issue the reboot until the operator authorizes it
and the pre-reboot outputs have been recorded.

### Pre-reboot

1. Run `hermes-vault status --json`, `hermes-vault integrity --json`, and
   `hermes-health`; record secret-free output.
2. Create a new synthetic `validation.reboot.marker` active memory with the
   same Session A upsert procedure above, then verify it with
   `hermes-vault search`.
3. Record `id -un`, `hostname`, `systemctl --user is-active
   hermes-gateway.service`, and the exact marker ID. Confirm no raw logs or
   runtime databases are being copied to the repository.
4. From a separate operator session, run the explicitly authorized command:

   ```bash
   sudo reboot
   ```

### Post-reboot

Wait for SSH and the graphical login to return, then run as `hermes`:

```bash
id -un
hostname
systemctl --user is-active hermes-gateway.service
hermes memory status
hermes-vault status --json
hermes-vault integrity --json
ss -ltnH | awk '$4 ~ /:9222$/ {print $4}'
curl -fsS http://127.0.0.1:9222/json/version >/dev/null
CHROME_DEVTOOLS_MCP_COMMAND="$HOME/.local/bin/chrome-devtools-mcp" \
  scripts/validate-chrome-mcp.sh
hermes-mcp-probe "$HOME/.local/bin/codex" --timeout 60 -- mcp-server
hermes sessions list --source validation --limit 5
hermes-health
```

The post-reboot marker query must be a fresh Hermes process using the indirect
question recorded before reboot. Its verbose output must show
`Hermes Vault — recalled 1 memory` and the exact marker value. Run
`hermes-graphical hermes computer-use doctor` with the captured graphical
session environment and retain a successful screenshot/state or equivalent
behavioral CUA probe. Confirm Chromium is active in the graphical session and
CDP remains loopback-only. Finally soft-forget the marker, verify active search
is empty, and rerun `hermes-vault integrity --json` and `hermes-health`.

Record each result as `PASS`, `BLOCKED_AUTH`, or `NOT TESTED` for any future
run. The executed 2026-08-27 run is fully accepted for this gate; its
post-reboot results and the non-blocking Codex authentication boundary are
documented in the linked evidence artifact.

## Failure interpretation

- A missing Codex credential is an external authorization boundary, not a
  reason to start OAuth automatically.
- A missing `DISPLAY` or graphical CUA helper is a physical/graphical proof
  boundary, not a Chrome MCP failure.
- A failed SQLite check stops maintenance and requires diagnosis; do not repair
  by deleting the database.
- A failed provider discovery after restart requires rollback to the exact
  pre-change backup and log capture.

Record command output in a private audit location first. Publish only
redacted, necessary status and never paste `.env`, `auth.json`, cookies,
browser output containing secrets, or full model/API responses.
