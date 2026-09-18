# OneKit concepts

This page explains the stable ideas behind OneKit. You do not need it to use
OneKit — the [README](../README.md) and [install guide](install.md) are enough.
Read this when you want to know why a command behaved the way it did, or before
[adding support](adding-support.md) for something new.

## The four objects

| Object | Answers | Lives in |
| --- | --- | --- |
| **Kit** | What do I want? | your `kit.yaml` |
| **Provider** | How can this be delivered? | `providers/` |
| **Capability** | Which providers are acceptable, in order? | `capabilities/` |
| **Client** | What does this AI client consume, and can OneKit write it? | `clients/` |

A Kit holds intent only. It may not contain MCP configuration blocks, client
paths, provider fallback logic, secrets or per-client exceptions. Those belong
to the definitions catalogue, which is versioned in Git.

## Delivery mechanisms

A provider declares one or more ways it can be delivered. v0 has three:

| Mechanism | Meaning | Payload |
| --- | --- | --- |
| `local-mcp` | The client directly launches or connects to a local MCP server | `command`, optional `args` |
| `remote-mcp` | The client directly connects to a network-reachable HTTPS MCP endpoint | `url` |
| `exec` | The client runs an executable tool | `command`, optional `args` |

These are *direct* paths. A connection brokered by an intermediary — an OpenAI
Secure MCP Tunnel, for example — is none of these, and is out of scope for v0.

A writer declares which mechanisms it can express. If a resolved item needs a
mechanism its client's writer does not support, OneKit reports that and writes
nothing. It never re-labels one mechanism as another to make it fit.

## Control

```text
control: write | manual | none
```

| Level | Meaning | Setup produces |
| --- | --- | --- |
| `write` | OneKit writes the configuration | A managed region in a file |
| `manual` | A person must complete it | An exact procedure with values filled in |
| `none` | No valid path exists | Nothing; the item stays visible as blocked |

`observable` records whether OneKit can read back what is actually configured.
Where it cannot, a completed item is **asserted** — a record of the person's own
statement, with a date — rather than verified. `onekit confirm` is never run on
someone's behalf before they say the work is done.

## Resolution

Per target, independently:

1. A tool request means that one provider, or blocked.
2. A capability request walks its provider list in order.
3. The first provider with a delivery mechanism the client consumes wins.
4. The client's control level is recorded with the decision.

There is no scoring, ranking, health check or model-driven selection. Given the
same Kit, the same definitions and the same client, resolution is deterministic.
The list decides at setup time; drift never decides.

## Compilation and writers

The resolver decides *what* a target should use. The compiler asks the client's
declared writer what that decision *looks like* for that client. Core never
learns a configuration format: that knowledge lives entirely in the writer, which
is why a new client usually needs one YAML file and no code.

## Managed regions

A writer owns a bounded region of a configuration file and nothing else. Four
rules hold for every writer:

1. Modify only what OneKit owns; never alter unmanaged configuration.
2. Verify ownership and integrity before replacing, and refuse on mismatch.
3. Remove entries when they leave the Kit.
4. If no managed region exists, create one without claiming existing content.

The representation is writer-specific. The TOML writer used by Codex CLI marks
its region with comments and a SHA-256 of the body:

```toml
# >>> onekit kit=my-kit target=laptop sha256=<digest>
[mcp_servers."search-a"]
command = "npx"

# <<< onekit
```

If you hand-edit inside that region, the digest stops matching and OneKit refuses
to overwrite your change until you resolve it or pass `--force`.

One file holds one region. Several targets may share it — the marker then lists
them, `target=desktop,laptop` — but two different Kits may not, and OneKit says so
rather than taking the file over.

## How `setup` runs

```text
validate  →  plan  →  show diff  →  consent (only for --force)  →  mutate  →  write lock
```

Every check that can run before a mutation does. Item ids that would collide with
lock metadata, malformed locks, unknown writers, unsupported writer/delivery
combinations and ownership conflicts are all rejected while the filesystem is
still untouched.

Targets that share one physical configuration file are planned together, so no
plan is ever computed against a stale copy of a file another plan will change.

If a write fails partway through a multi-file setup, OneKit rolls back the files
it already wrote and reports that nothing remains applied. If a rollback cannot
be completed safely — because something else changed the file in between — OneKit
names the exact files needing attention instead of guessing. Configuration and
lock never diverge because of a failure: the lock is written only after every
mutation has succeeded.

## Operations

| Class | Operations | May an agent run it unattended? |
| --- | --- | --- |
| READ | `probe`, `resolve`, `diff`, `doctor` | Yes |
| SAFE WRITE | `setup`, `confirm` | Yes |
| DESTRUCTIVE | `setup --force` | No |

SAFE WRITE means writing only inside a OneKit-owned boundary whose ownership and
integrity verify, or creating a new boundary without altering unmanaged content.

`--force` is destructive. It shows the diff first, then requires a human to type
`FORCE` at a terminal, and it fails outright in a non-interactive session. No
environment variable or flag combination overrides that.

## The lock

The lock records decisions, never credentials.

```yaml
lock_version: 1
definitions: onekit-defs@a91f3c7
accepted_at: 2026-09-18T09:14:00Z

web-search:
  requested: [search-a, search-b]
  laptop:
    provider: search-a
    delivery: local-mcp
    control: write
    setup: complete
```

`setup` state is separate from resolution, because resolved and configured are
different facts and only one of them is about the world. Whether a provider was
the first choice is derived from `requested`, not stored.

### Definitions provenance

`definitions` identifies the catalogue that produced the decisions:

* From a Git checkout, it is the exact commit, and OneKit refuses to lock while
  the catalogue has uncommitted changes.
* From an installed release, there is no repository, so it is the distribution
  version — `onekit-defs@v0.0.2` — which identifies the catalogue that shipped
  inside that release.

### Reproducibility boundary

A lock reproduces OneKit's *resolution decision*. It does not pin the provider
packages or services themselves: `npx -y some-mcp-server` resolves to whatever is
current when it runs. This is a real limitation of v0 and is not solved here.

## The configuration lock file

While writing, a writer holds an exclusive lock on a sidecar next to the
configuration file — `config.toml.onekit.lock` for Codex CLI. The sidecar is
**left in place on purpose**. Deleting it while another process might already
hold a descriptor on it would silently break mutual exclusion, so it persists as
a small empty file. It contains nothing and is safe to ignore.

## Drift

`onekit doctor --fixture search-a-down` demonstrates drift reporting: OneKit names
the locked provider, the observed state, and the next provider in the list, then
changes nothing. There is no runtime health framework in v0 — defining what
"healthy" means for a local MCP server versus a remote endpoint versus a CLI tool
is a project, not a feature. Doctor reports and stops.
