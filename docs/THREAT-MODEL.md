# Threat model

## UNSAFE BY DESIGN

This reference configuration can give an LLM-controlled Hermes Agent
unrestricted passwordless root access and control of an authenticated browser
through Chrome DevTools.

A successful prompt injection, malicious MCP server, compromised dependency,
or agent mistake can become full compromise of the VM and any credentials or
data reachable from it. Do not deploy it on a workstation, shared server, or
machine containing unrelated secrets. Use a dedicated or disposable VM with a
deliberately limited trust boundary.

## Boundaries

- Root: the `hermes` account may use unrestricted `sudo`; prompt injection can
  therefore become root execution.
- Browser: loopback CDP and DevTools MCP can inspect or modify the authenticated
  Chromium profile. Loopback binding is network containment, not authorization.
- External content: webpages, browser output, web results, and external MCP
  responses are hostile data, not instructions.
- MCP: servers run as local processes and can have the authority of the data or
  integrations they expose.
- Credentials: anything reachable by `hermes` or root inside the VM is within
  the agent's potential blast radius.

The VM/hypervisor/network boundary is the containment strategy. Keeping the
gateway unprivileged reduces accidental service privilege but does not make
the agent safe: its terminal can still invoke `sudo`.

## Mitigations and residual risk

The project keeps CDP loopback-only, separates private runtime state from the
public tree, redacts common secrets before vault persistence, labels recalled
untrusted data, uses SQLite integrity checks and backups, and preserves CUA as
a fallback rather than claiming that any browser path is safe. These are
containment and recovery measures, not a security guarantee. Redaction cannot
recall a secret already sent to a model, browser, or MCP server, and rollback
does not restore browser cookies or profile databases.
