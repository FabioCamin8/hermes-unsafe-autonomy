# Codex escalation

Hermes remains the primary orchestrator. Codex is a specialist MCP for complex
research, coding, and debugging tasks after Hermes has gathered the local
facts. Hermes owns applying changes, verifying them, and deciding whether a
result is trustworthy.

The configured local stdio command is:

```text
$HOME/.local/bin/codex mcp-server
```

Bootstrap pins `@openai/codex@0.150.1`, registers it through Hermes' native
`mcp_servers` configuration, and verifies protocol initialization/tool
discovery. The MCP probe does not authenticate an account. If useful Codex
work requires credentials, the operator must perform that separate action;
this project never starts OAuth, copies auth files, or puts tokens in Git.

Codex is not the authoritative memory store and is not required for ordinary
web lookup. If it is unavailable, Hermes continues with its native tools,
durable vault, Chrome MCP, and CUA fallback; an escalation failure is recorded
as an unavailable specialist rather than silently treated as success.
