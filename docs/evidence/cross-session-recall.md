# Cross-session recall evidence

Status: PASS

This is a sanitized live acceptance record. The value is intentionally
synthetic, no runtime database or raw conversation log is published, and the
temporary live record was removed after the probe.

## Run metadata

- Timestamp: 2026-08-27T14:51:49Z to 2026-08-27T14:52:01Z
- Hermes Agent: 0.20.5 (local source `1bbb6e5b`)
- Hermes Vault: 0.1.0
- Provider/schema: `hermes_vault`, SQLite schema 1, FTS5
- Synthetic key: `validation.cross_session.marker`
- Synthetic value: `validation-synthetic-value`

The session, process, and record identifiers in the excerpt below are
generalized placeholders for publication; the underlying live run used
distinct values and was cleaned up afterward.

## Procedure

Run as `hermes` on the dedicated VM:

1. Start a fresh Session A process with `hermes chat -q ... -Q`, asking the
   active memory provider to upsert the synthetic observation. Capture its
   process ID and session ID, then wait for the process to exit.
2. Confirm the active record with `hermes-vault search` and retain only its
   non-sensitive ID and status.
3. Start a new Session B process with a new process ID and session ID. Ask:
   `From the context provided to you at turn start, copy character-for-character
   the token after the words Synthetic validation value for the record keyed
   validation.cross_session.marker.` The prompt did not request a vault search,
   `vault_search`, or any memory tool call.
4. Filter verbose output to the deterministic recall indicator, final
   `value=` line, and Session ID. Do not publish the raw verbose log.
5. Soft-forget the recorded ID through `VaultStore.forget(...,
   hard_delete=False)`, verify the key has no active result, and run the
   provider integrity check.

The exact command shape is documented in [VALIDATION.md](../VALIDATION.md).

## Expected behavior

Session B must be a distinct fresh process/session. Without calling an
explicit vault or memory-search tool, Hermes must automatically prefetch the
active synthetic record, emit its provider recall indicator, and return the
exact synthetic value written by Session A. Cleanup must leave no active
synthetic record, and the vault must remain SQLite-integrity healthy.

## Observed behavior

Session A exited successfully before Session B started. Session B was a
distinct process and session, emitted `Hermes Vault — recalled 1 memory`, and
returned the exact value written by Session A. Soft-forget removed the active
record and the final integrity check passed.

## Direct sanitized excerpts

```text
session_a_pid=pid-A
session_a_exit=0
session_a_id=session-A
session_a_result=STORED
durable_active_id=synthetic-record-id

session_b_pid=pid-B
session_b_exit=0
session_b_id=session-B
recall_indicator=Hermes Vault — recalled 1 memory
session_b_value=validation-synthetic-value
exact_value_match=true

soft_forget=True
active_after_cleanup=[]
integrity={"ok": true, "sqlite": "ok"}
```

The two session IDs and process IDs are distinct. Session A ended before
Session B started. The recall indicator is emitted by Hermes independently of
the model's answer, and the returned value exactly matches the value written
in Session A. Cleanup completed; no active synthetic marker remains.
