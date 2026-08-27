# Hermes unsafe autonomy

## UNSAFE BY DESIGN

This reference configuration can give an LLM-controlled Hermes Agent
unrestricted passwordless root access and control of an authenticated browser.
Prompt injection, a malicious MCP server, a compromised dependency, or an
agent mistake can become full compromise of the VM and reachable data. Use a
dedicated or disposable VM with a deliberately limited trust boundary; this
is not a workstation or shared-server security model.

This repository installs a deliberately unsafe, local-first autonomy substrate
for a dedicated graphical Hermes Agent VM. It adds durable profile-scoped
memory, browser MCP, Codex MCP escalation, passwordless root for the Hermes
user, recovery tooling, and auditable validation.

This is not a sandbox. After installation, any Hermes tool or prompt that can
run a terminal command can use `sudo` as root. Use it only on a dedicated VM
whose data and network access are intentionally in scope.

## What is installed

| Component | Owner and boundary |
| --- | --- |
| `hermes_vault` | A user Hermes `MemoryProvider` under `$HERMES_HOME/plugins/`; SQLite/FTS5 is the index, Markdown is a readable materialization, and JSONL is an append-only redacted journal. |
| Root escalation | `/etc/sudoers.d/hermes-unsafe-autonomy`, installed only with `--enable-unsafe-root`; the Hermes gateway remains a user systemd service running as `hermes`. |
| Chrome DevTools MCP | `chrome-devtools-mcp` pinned to `1.8.0`, using loopback CDP at `127.0.0.1:9222` and the documented statistics-off flags. |
| CUA fallback | The pre-existing graphical `hermes-graphical` helper and Hermes Computer Use configuration; SSH-only sessions cannot prove graphical CUA. |
| Codex MCP | `@openai/codex` pinned to `0.150.1`, exposed as `codex mcp-server`; the installer never authenticates an account. |
| Recovery | Private point-in-time backups, SQLite integrity checks, rollback, maintenance, and an uninstall path that preserves sessions and browser profiles. |

The two MCP servers are configured as local stdio processes. Browser and MCP
content is data, not authority: the vault labels web/browser/external-MCP
records `untrusted`, and recalled records cannot override system, developer, or
user instructions.

## Prerequisites

The target must already be a dedicated graphical Debian VM with:

- a `hermes` user and `$HOME/.hermes` owned by that user;
- Hermes installed at `/home/hermes/.local/bin/hermes`;
- a user systemd manager and the existing `hermes-gateway.service`;
- Chromium running with loopback-only CDP on `127.0.0.1:9222`;
- the `hermes-graphical` CUA helper and a graphical session;
- root access for the operator who runs the bootstrap.

The bootstrap installs `sudo`, Python/SQLite support, `curl`, `ss`, Node, and
npm if they are missing. Node 20 or newer is required by Chrome DevTools MCP.

## Install

Transfer this tracked source tree to the VM without copying `.env` files,
Hermes credentials, sessions, or a Chromium profile. From the repository root,
run as root:

```bash
sudo ./bootstrap.sh --enable-unsafe-root
```

Bootstrap is intentionally explicit about the dangerous choice. It refuses a
different active memory provider or a conflicting existing `chrome-devtools`
or `codex` MCP entry, creates a private pre-change backup first, installs the
provider and pinned npm packages as `hermes`, writes only the selected Hermes
config leaves, reloads the user timer, and restarts the user gateway.

It does not call `hermes auth`, `codex login`, an OAuth flow, or any browser
login. Codex MCP can be configured and protocol-probed without credentials;
an operator must separately decide whether to authenticate it.

## Configuration contract

The resulting Hermes config has these relevant values. The CDP port defaults
to `9222` and can be changed with `HERMES_CDP_PORT`; the address remains
loopback-only:

```yaml
memory:
  memory_enabled: true
  provider: hermes_vault

compression:
  enabled: true
  checkpoint_required: true

browser:
  backend: off
  cdp_url: http://127.0.0.1:9222

computer_use:
  grant_existing_profile: true
  cua_telemetry: false
```

The MCP entries point to `/home/hermes/.local/bin/chrome-devtools-mcp` and
`/home/hermes/.local/bin/codex`. The paths are examples for the authorized
VM, not portable credentials or public endpoints.

## Daily operation

Run these commands as `hermes` or through the user’s normal login session:

```bash
hermes-health
hermes-vault status --json
hermes-vault integrity --json
hermes mcp list
```

The scheduled user timer runs local integrity, cleanup, and vault backup
maintenance. `hermes-health` is a secret-free aggregate of gateway, provider,
vault, CDP/MCP, CUA, root-mode, and backup state. See
[docs/OPERATIONS.md](docs/OPERATIONS.md) for backup, rollback, CUA, MCP, and
uninstall procedures.

## Verification

Local source validation is:

```bash
scripts/validate.sh
scripts/validate-public-tree.sh
scripts/validate-public-history.sh
```

After bootstrap, the live gates are described in
[docs/VALIDATION.md](docs/VALIDATION.md) and recorded in
[docs/LIVE-AUDIT.md](docs/LIVE-AUDIT.md). The public-tree and public-history
scans must pass before anything is committed. This runtime layer is released
independently from the companion
[hermes-unsafe-vm](https://github.com/FabioCamin8/hermes-unsafe-vm) provisioner,
which installs a pinned release rather than copying this source.

Initial v0.1.x Git metadata was canonicalized shortly after publication to
replace machine-generated author and tag identities with the project's GitHub
noreply identity. Source functionality and release intent were preserved; the
correction was a one-time initial-publication cleanup.

## Documentation map

The main subjects are intentionally consolidated rather than duplicated across
thin files:

- Architecture: [README.md#architecture](README.md#architecture) and
  [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).
- Memory architecture and operations: [docs/OPERATIONS.md#memory-operations](docs/OPERATIONS.md#memory-operations),
  [docs/RESEARCH.md](docs/RESEARCH.md), and
  [docs/evidence/cross-session-recall.md](docs/evidence/cross-session-recall.md)
  plus [docs/evidence/checkpoint-failure.md](docs/evidence/checkpoint-failure.md).
- MCP and browser boundary: [docs/CHROME-DEVTOOLS.md](docs/CHROME-DEVTOOLS.md)
  and [docs/CODEX-ESCALATION.md](docs/CODEX-ESCALATION.md).
- Backup and recovery: [docs/OPERATIONS.md#backups](docs/OPERATIONS.md#backups)
  and [docs/OPERATIONS.md#rollback](docs/OPERATIONS.md#rollback).
- Validation, reboot runbook, and acceptance terminology:
  [docs/VALIDATION.md](docs/VALIDATION.md) and
  [docs/LIVE-AUDIT.md](docs/LIVE-AUDIT.md).
- Publication checklist: [docs/PUBLICATION-CHECKLIST.md](docs/PUBLICATION-CHECKLIST.md).

## Recovery and non-goals

Use `scripts/backup.sh` before a planned change and
`scripts/rollback.sh --backup-dir ... --restart` to restore a selected private
backup, including the pinned MCP runtime and its launcher links. Rollback does
not restore or delete the Chromium profile, and it does not remove the unsafe
sudo rule; those are separate, explicit operator choices.

This project does not provide cloud memory, embeddings, remote replication,
automatic account authentication, a security sandbox, or automatic promotion
of recalled content into trusted instructions. See
[SECURITY.md](SECURITY.md) and [docs/LIMITATIONS.md](docs/LIMITATIONS.md).
