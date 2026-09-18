# OneKit

## One AI Toolkit, Across Every AI You Use

**Build your Kit once. Take it wherever you work with AI.**

OneKit is a small, extensible portability layer for AI toolkits. Describe the tools and capabilities you want once. OneKit resolves them for each AI client, configures what it can, gives you setup steps for what requires a person, and shows every gap.

> **OneKit is not your toolkit. OneKit makes your toolkit portable.**

OneKit v0.0.1 supports Codex CLI and ChatGPT with a Git-versioned definitions catalogue.

---

## The Short Version

A Kit holds your intent:

```yaml
name: my-kit
targets:
  - id: laptop
    client: codex-cli
  - id: work-chat
    client: chatgpt
tools:
  - github
  - local-tool
capabilities:
  - web-search
```

`tools` are specific provider choices. `capabilities` are outcomes for which an ordered list of providers can be accepted. OneKit resolves each request independently for each target. The example Kit produces:

```text
laptop / github: github (remote-mcp, write)
laptop / local-tool: local-tool (local-mcp, write)
laptop / web-search: search-a (local-mcp, write)
work-chat / github: github (remote-mcp, manual)
work-chat / local-tool: blocked
work-chat / web-search: search-b (remote-mcp, manual)
```

The Kit stays the same. The client outcomes differ. Nothing is silently dropped.

---

## Why OneKit Exists

AI clients increasingly support external tools through MCP, skills, plugins and executable tools, but they do not support or configure them in the same way. One client may launch a local MCP server from a writable file. Another may require a remote HTTP MCP connection through its UI. A local-only tool may have no valid path to a web client.

MCP helps standardise how tools communicate. OneKit answers the remaining question:

> **Given the toolkit I want, how can it be realised in each AI environment I use?**

---

## How OneKit Works

```text
Kit (what you want)
  +
Git-versioned definitions (what exists and what each client consumes)
  ↓
resolver
  ↓
per-client configuration + manual procedures + gaps
```

The definitions catalogue contains three declarative objects.

### Provider

A provider says how something can be delivered. This is a schema illustration:

```yaml
id: example-provider
delivery:
  local-mcp:
    command: npx
    args: ["-y", "example-mcp"]
  remote-mcp:
    url: https://example.org/mcp
```

In v0, `local-mcp` means the client directly launches or connects to a local server. `remote-mcp` means it directly connects to a network-accessible HTTP endpoint. Secure MCP Tunnel is a distinct mechanism and is outside v0.

### Capability

A capability is an ordered list of acceptable providers:

```yaml
id: web-search
providers: [search-a, search-b]
```

OneKit does not prove the providers equivalent. Putting them in one capability is a reviewable assertion that either can satisfy the requested outcome.

### Client

A client declares what it consumes and how configuration is controlled:

```yaml
id: example-client
consumes: [local-mcp, remote-mcp]
control: write
observable: true
config: ~/.example-client/config.toml
writer: toml-region
```

The Kit, provider, capability and client definitions remain separate. The Kit contains no client paths, MCP blocks, fallback logic or secrets.

---

## Resolution Is Intentionally Boring

For a tool, OneKit tries the requested provider. For a capability, it walks the provider list in order, then each provider's delivery mechanisms in declaration order. The first delivery mechanism the client consumes wins. If none exists, the request is blocked.

No scoring, quality ranking, runtime health check or AI tool selection changes this decision. Given the same Kit, definitions commit and client, resolution produces the same result.

The v0 web-search fixture shows the difference: `search-a` has only local MCP and is selected for Codex CLI. ChatGPT consumes direct remote MCP, so it skips `search-a` and selects `search-b`. The reason is recorded; the Kit is unchanged.

---

## Write, Manual, or Blocked

| Control | Meaning |
| --- | --- |
| `write` | OneKit writes an owned configuration region. |
| `manual` | OneKit produces a procedure that a person completes. |
| `none` | No valid setup path exists; the request is blocked. |

Resolved and configured are different facts. A manual item starts pending. After completing its procedure, the person can run `onekit confirm TARGET ITEM`. If OneKit cannot read back that client's configuration, `doctor` calls the result **asserted**, not verified.

Codex CLI uses a writable TOML configuration file. ChatGPT web/workspace setup uses its UI and remains manual from OneKit's perspective. `probe` cannot establish whether a particular ChatGPT workspace is accessible or approved.

---

## OneKit Configures, Then Gets Out of the Way

OneKit is a configuration control plane, not an execution proxy. After setup, clients connect to providers using their selected delivery mechanisms. OneKit does not sit between every tool call, export browser sessions, or make a local-only server appear remote.

---

## Safe Configuration Ownership

Each writer uses a format-appropriate ownership boundary and integrity check. For Codex TOML, the boundary uses TOML comments and a SHA-256 hash:

```toml
# >>> onekit kit=my-kit target=laptop sha256=<digest>
[mcp_servers."search-a"]
command = "npx"
args = ["-y", "@brave/brave-search-mcp-server", "--transport", "stdio"]

# <<< onekit
```

Before replacement, OneKit verifies ownership and integrity. It refuses to overwrite a managed region that has been edited outside OneKit. It does not claim or alter unmanaged entries, and it removes owned entries that leave the Kit.

`onekit diff` reads the proposed changes without writing. `setup` also shows that diff before applying it. `--force` is destructive: it requires interactive human confirmation and fails in non-interactive sessions.

---

## Agent-Driven Installation

Give a coding agent the public [install document](https://raw.githubusercontent.com/365yoyo/onekit/main/docs/install.md). It tells the agent how to clone OneKit, install it in a virtual environment, run the read-only `onekit probe`, report what it found, and stop. Installation does not run `setup` or change client configuration.

To configure a Kit after installation, start with [the example Kit](examples/kit.yaml). Run `.venv/bin/onekit --kit examples/kit.yaml resolve` and then `.venv/bin/onekit --kit examples/kit.yaml diff` from the checkout. Run `setup` with the same `--kit` option only after reviewing the proposed changes.

---

## Operations and Doctor

| Class | Operations | Agent unattended? |
| --- | --- | --- |
| READ | `probe`, `resolve`, `diff`, `doctor` | Yes |
| SAFE WRITE | `setup`, `confirm` | Yes |
| DESTRUCTIVE | `--force` | No |

`doctor` reports both **available** and **configured** counts. Where a manual client's state cannot be read programmatically, a person's `confirm` records an asserted completion.

With a lock, the v0.0.1 drift demonstration is fixture-driven:

```text
$ onekit doctor --fixture search-a-down

Web Search / laptop

  locked:       search-a
  observed:     failing (fixture)
  next in list: search-b

  Note: tool names and schemas differ.

  No change made.
```

There is no runtime health monitoring or automatic failover in v0. Doctor reports and stops.

---

## Extensibility Over Coverage

The design invariant is:

> **Novel integration mechanics extend OneKit. They do not modify OneKit Core.**

Adding a provider or capability normally takes one YAML file. A new client takes a client definition and an existing writer, or a new reusable writer if its storage format is new. The resolver does not learn client-specific syntax.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the definition formats and contribution steps.

---

## Repository Shape

```text
onekit/
├── core/            loader, resolver, compiler, doctor, CLI
├── clients/         client definitions
├── providers/       provider definitions
├── capabilities/    ordered provider lists
├── writers/         format-specific managed regions
├── skills/onekit/   agent workflow
├── docs/install.md  URL-first installation
├── examples/kit.yaml
└── CONTRIBUTING.md
```

The user-facing entry points are this README, the install URL, a Kit and `doctor`. The catalogue and writers are for contributors.

---

## What v0.0.1 Does Not Solve

There is no `control: agent`, runtime health monitoring, automatic failover or update, provider version pinning, capability contracts, conformance suite, hosted registry, marketplace, execution proxy or web UI.

A lock pins the definitions commit and reproduces OneKit's resolution decision. It does not pin provider packages or services, so it cannot guarantee a byte-for-byte reproduction of their implementations.

Kits contain no secrets. Locks contain decisions, not credentials. Clients authenticate directly to providers. Provider output remains untrusted, and inclusion in a capability list is a compatibility assertion rather than a safety guarantee.

---

## Build Once. Extend Everywhere.

OneKit should be smaller than the ecosystem built around it. Other people can extend providers, capabilities, clients and writers without making Core larger.

**Build your Kit once. Take it wherever you work with AI.**
