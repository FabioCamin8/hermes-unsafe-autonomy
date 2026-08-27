# Fail-closed checkpoint evidence

Status: PASS

## Run metadata

- Timestamp: 2026-08-27T15:01:09Z
- Hermes Agent: 0.20.5 (local source `1bbb6e5b`)
- Checkpoint API: v2
- Configuration under test: `compression.checkpoint_required=true`
- Runtime state: no database, vault, session, or browser state was touched

## Procedure

The public regression test
`test_required_checkpoint_failure_blocks_lossy_compression` injects a
deterministic `OSError` into the real `VaultStore.checkpoint()` call made by
`HermesVaultProvider.on_pre_compress()`. It verifies the call, visible
propagation, skipped lossy callback, and unchanged transcript.

Separately, on the installed Hermes source, a temporary v2
`MemoryProvider.on_pre_compress()` raised the same synthetic failure while the
real `agent.conversation_compression.compress_context()` function was called
with `compression_checkpoint_required=True`. The temporary compressor was a
spy and no summary model or network call was allowed. The installed
`MemoryManager` was also exercised with no active provider to cover its
distinct missing-provider branch.

## Direct sanitized excerpts

```text
Required pre-compress checkpoint failed (OSError)
INSTALLED_COMPRESS_CONTEXT_FAILURE=PASS
INSTALLED_COMPRESS_CONTEXT_TRANSCRIPT_PRESERVED=PASS
INSTALLED_COMPRESS_CONTEXT_LOSSY_CALLBACK_SKIPPED=PASS
INSTALLED_MEMORY_MANAGER_CHECKPOINT_FAILURE=PASS
INSTALLED_MEMORY_MANAGER_MISSING_PROVIDER=PASS
```

The production compressor raises before the lossy compressor is reached, so
the uncompressed transcript remains the authoritative value. The missing
provider branch is rejected separately rather than treated as a successful
checkpoint.
