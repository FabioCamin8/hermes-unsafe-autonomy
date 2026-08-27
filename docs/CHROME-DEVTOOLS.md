# Chrome DevTools MCP

Chrome DevTools MCP is the primary structured browser path:

```text
Hermes -> Chrome DevTools MCP -> 127.0.0.1:9222 -> existing Chromium
                                  |
                                  +-> CUA/X11 fallback when structure is insufficient
```

The bootstrap pins `chrome-devtools-mcp@1.8.0`, connects with
`--browser-url=http://127.0.0.1:9222`, and disables usage statistics and
Performance CrUX. The endpoint must remain loopback-only; this repository does
not add a firewall exception, port forward, or tunnel.

Set `HERMES_CDP_PORT` during bootstrap and validation to use another loopback
port. The address is intentionally fixed to `127.0.0.1`.

`scripts/validate-chrome-mcp.sh` proves CDP reachability, MCP initialization,
tool discovery, and a harmless behavior smoke: list pages, select one without
bringing it forward, take a structured snapshot, evaluate only title and URL,
and list console/network records. It never navigates, writes page data, or
prints authenticated titles, URLs, console messages, or network payloads.

The pinned package officially targets Chrome and Chrome for Testing. Debian
Chromium is an empirical compatibility result on this VM (`151.0.7922.169`),
not an upstream support claim. DevTools has the authority of the attached
authenticated profile, so even read-oriented inspection can disclose private
data. CUA remains available for native X11/accessibility or visual interaction;
an SSH-only shell without a graphical environment cannot prove CUA.
