# Limitations and known boundaries

- Passwordless root is intentionally unrestricted. It is a capability, not a
  safety control.
- The gateway remains user-scoped, but a compromised or misdirected Hermes
  terminal can still become root through `sudo`.
- Redaction recognizes common secret formats only. It cannot identify every
  secret, remove a value already sent to a model, or protect a secret pasted
  into an external browser/MCP service.
- The durable provider is local SQLite/FTS5. It has no cloud replication,
  embeddings, multi-host consensus, or remote disaster recovery.
- Native Hermes memory and session history remain active. The vault augments
  them; it does not replace or automatically reconcile every native record.
- Chrome DevTools MCP officially targets Chrome/Chrome for Testing. Debian
  Chromium support is an empirical validation on this VM and may drift with a
  browser update.
- CUA requires a real graphical session. SSH-only `doctor`/shell output proves
  neither screenshots nor UI action capability.
- Codex MCP is an experimental stdio surface and may require explicit external
  authentication. This project never initiates OAuth automatically.
- Backups exclude the Chromium profile by design. Rollback cannot restore
  browser cookies, tabs, or profile databases.
- Hard deletion affects the active vault database/materialization only. A
  backup, journal copy, model transcript, or external service may retain prior
  content.
- A failed post-change gateway restart is not silently auto-rolled back.
  Preserve logs, select the exact private backup, and run rollback deliberately.
- Version pins are explicit but not self-updating. Upgrade them only with a
  new backup, research, MCP probe, and acceptance run.
