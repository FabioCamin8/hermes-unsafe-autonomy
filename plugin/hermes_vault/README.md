# Hermes Vault provider

`hermes_vault` is a local Hermes `MemoryProvider`. It stores structured
records in SQLite/FTS5, materializes human-readable Markdown, and appends
redacted evidence to a JSONL journal. It does not require a cloud service,
embeddings database, or Obsidian.

The provider receives `hermes_home` from Hermes during `initialize()` and
stores state under its active profile's `vault/` directory. It advertises
pre-compression checkpoint API v2, so Hermes can fail closed before lossy
compression when `compression.checkpoint_required` is enabled.

Untrusted web, browser, and external-MCP records remain labeled
`untrusted`; their content is data and never an instruction source.
