# Changelog

## 0.1.1

- Fix bootstrap initialization ordering so the configured Hermes executable is
  resolved before strict-shell validation uses it.

## 0.1.0

- Published the validated Hermes unsafe-autonomy runtime with durable local
  memory, recovery tooling, browser MCP, CUA integration, and Codex MCP.
- Public history is intentionally re-rooted from the internally validated
  source after publication review; private amended commits and runtime state
  are not part of the release history.
- Codex specialist execution remains an operator-controlled authentication
  boundary and is never authenticated by bootstrap.
