# Operations

All paths below use the installed Hermes user profile. Run profile operations
as `hermes`; use root only for package installation and the explicit sudoers
rule.

## Health

Use the aggregate check for a secret-free operational summary:

```bash
hermes-health
```

It performs gateway, CDP, MCP, and CUA checks, while the provider status call
refreshes its private `health.json` state. `Overall: PASS` requires the
gateway, vault, loopback CDP, both configured MCP processes, and unsafe-root
capability to pass. CUA may be `DEGRADED` without a graphical session; it is
required to be `OK` in the graphical session.

For detailed provider evidence:

```bash
export HERMES_HOME=/home/hermes/.hermes  # default; omit when the account home differs
hermes-vault status --json
hermes-vault integrity --json
hermes config get memory.provider
hermes config get compression.checkpoint_required
hermes mcp list
```

Healthy output must show `hermes_vault`, checkpoint-required `true`, valid
SQLite integrity, the user gateway active, and the two expected local MCP
commands. `hermes status` may report unrelated provider advisories; record
those separately instead of hiding them.

Hermes’ `Sudo:` row reports whether the `SUDO_PASSWORD` environment variable
is present for password-based terminal helpers. This installation intentionally
does not set that secret. Prove the requested passwordless capability directly:

```bash
sudo -n id -u
```

The required result is `0` when run as `hermes`.

## Memory operations

The provider automatically journals turns and exposes bounded durable tools to
Hermes. It is an augmenting provider: native `MEMORY.md`, `USER.md`, and
session search remain active.

The local CLI is useful for inspection and recovery:

```bash
hermes-vault search "operator preference"
hermes-vault get MEMORY_ID
hermes-vault checkpoint "manual boundary" "verified evidence"
hermes-vault reindex
```

Use the Hermes `vault_*` tools for normal upsert, supersession, provenance,
trust, and forget operations. Soft forget is the default. Confirmed hard
deletion requires the literal `FORGET` and cannot erase copies already present
in backups or remote transcripts.

## Backups

```bash
scripts/backup.sh --label before-change
```

The script creates a private directory under
`$HERMES_HOME/backups/<label>-<UTC timestamp>`, uses SQLite’s backup API for
databases, and records a manifest. It copies Hermes config/native state,
provider state, user units, project-owned helpers, and the pinned MCP runtime
tree, preserving the launcher symlinks that point into that tree. It never
copies the Chromium profile.

The user timer invokes the same provider maintenance path:

```bash
systemctl --user status hermes-vault-maintenance.timer
systemctl --user start hermes-vault-maintenance.service
```

Maintenance refuses to continue on failed SQLite integrity and retains the
newest seven vault backups by default.

## Rollback

Resolve the exact private backup first, then run:

```bash
scripts/rollback.sh \
  --backup-dir "$HERMES_HOME/backups/pre-autonomy-YYYYMMDDTHHMMSSZ" \
  --restart
```

Rollback backs up the current state before restoring config, the project
provider, vault materialization, user units, project-owned helper files, and
the pinned MCP runtime tree. It recreates the `codex` and
`chrome-devtools-mcp` launcher symlinks instead of copying their targets. It
does not touch sessions, unrelated plugins, the browser profile, npm’s global
package cache, or the unsafe sudoers rule. Verify the gateway and vault after
rollback.

## Chrome MCP and CUA

Chrome DevTools MCP is the preferred browser path. Validate it with:

```bash
CHROME_DEVTOOLS_MCP_COMMAND="$HOME/.local/bin/chrome-devtools-mcp" \
  HERMES_CDP_PORT=9222 scripts/validate-chrome-mcp.sh
```

The check proves loopback CDP reachability, loopback-only binding, MCP stdio
initialization/tool discovery, and an opt-in harmless browser smoke: page
listing, selection without foregrounding, structured snapshot, pure title/URL
evaluation, and console/network inspection. It does not prove that a model
selected a safe browser action.

When DevTools MCP cannot express an interaction, use the graphical helper and
Hermes Computer Use against the existing profile. A graphical login/session
must be present; an SSH-only shell with no `DISPLAY` is not CUA proof. Keep
the CDP endpoint loopback-only during both modes.

## Codex MCP

The configured command is:

```text
$HOME/.local/bin/codex mcp-server
```

The package and command are pinned by bootstrap. The installer may probe MCP
initialization, but it never calls an authentication command. If the Codex
CLI reports missing credentials, that is an external operator decision and
must be recorded as `BLOCKED_EXTERNAL_AUTH`, not “fixed” by an unattended
OAuth flow.

## Uninstall

Back up first. Then select only the integrations to remove:

```bash
scripts/uninstall.sh --yes
scripts/uninstall.sh --yes --remove-mcp --remove-timer --remove-unsafe-root
```

The script removes the project provider/CLI and selected MCP/timer/sudoers
state, while preserving native Hermes sessions and browser data. Use rollback
for configuration restoration rather than deleting the profile.
