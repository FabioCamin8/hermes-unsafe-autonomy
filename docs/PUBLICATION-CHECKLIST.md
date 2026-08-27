# Publication checklist

Run this checklist from the repository root before publishing or committing a
public tree. Runtime state belongs to the private Hermes profile, never this
repository.

## Repository and runtime exclusion

- [ ] `git status --short` is empty after intended changes are committed.
- [ ] No runtime vault, SQLite database, `-wal`, or `-shm` file is tracked.
- [ ] No browser profile, cookies, or browser databases are tracked.
- [ ] No OAuth state, `auth.json`, Codex auth state, or personal session/log
  files are tracked.
- [ ] No API keys, tokens, credentials, SSH private keys, or live backups are
  tracked.
- [ ] `scripts/validate-public-tree.sh` reports `PUBLIC_TREE_SCAN=PASS`.
- [ ] `scripts/validate-public-history.sh` reports `HISTORY_SCAN=PASS`, including
  the generated `git archive` path scan.
- [ ] Every reachable commit uses `FabioCamin8` and the canonical GitHub
  noreply email for both author and committer.
- [ ] Every release tag is annotated and uses the same canonical tagger.

## Safety and compatibility claims

- [ ] The `UNSAFE BY DESIGN` warning is present and explains the blast radius.
- [ ] Unrestricted root mode is documented as explicit bootstrap opt-in.
- [ ] CDP is documented and validated as loopback-only.
- [ ] Chromium compatibility is described as empirical Debian Chromium
  compatibility, not an upstream Chrome support claim.
- [ ] Codex MCP protocol initialization is distinguished from authenticated
  specialist execution; missing credentials remain `BLOCKED_AUTH` and no OAuth
  is started automatically.
- [x] VM reboot recovery is documented as `PASS` after the controlled reboot
  acceptance procedure was executed; see `docs/evidence/vm-reboot-recovery.md`.

## Required proof commands

```bash
git rev-parse HEAD
git rev-parse HEAD^{tree}
git status --short
python3 -m unittest discover -s tests -v
scripts/validate-public-tree.sh
```

The live VM proof additionally requires provider status/integrity,
`hermes-health`, loopback CDP plus the Chrome MCP behavioral probe, CUA in a
graphical session, Codex MCP initialization, and the completed documented
post-reboot runbook.

## Initial-publication history correction

The initial v0.1.x Git metadata was canonicalized shortly after publication
to replace machine-generated author and tag identities with the project's
GitHub noreply identity. Source functionality and release intent were
preserved. This was a one-time correction; future release tags are immutable.
