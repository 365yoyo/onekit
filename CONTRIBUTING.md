# Contributing to OneKit

OneKit grows mostly by adding data, not code. A new provider or capability is one
YAML file and no core change; a new client is usually one YAML file and an
existing writer.

**[docs/adding-support.md](docs/adding-support.md) is the reference for all four
cases** — provider, capability, client and writer — including the writer
interface. Start there. [docs/concepts.md](docs/concepts.md) explains the ideas
those formats encode.

## Where things live

```text
providers/       how something can be delivered
capabilities/    ordered lists of acceptable providers
clients/         what an AI client consumes, and whether OneKit can write it
writers/         format-specific managed configuration
core/            loader, resolver, compiler, doctor, CLI
```

The catalogue directories stay at the top level for you to edit, and ship inside
the installed package so a released OneKit can find them.

## Working on it

```sh
git clone https://github.com/365yoyo/onekit.git
cd onekit
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests
```

`ONEKIT_SKIP_INSTALL_TEST=1` skips the packaging test, which builds a virtual
environment and needs a package index.

## What a good pull request looks like

* **One concern.** A provider, a capability, a client, a writer, or a fix.
* **Evidence for client definitions.** Verify the configuration path, format,
  control level, readback behaviour and any policy or authentication
  requirements against the real client, and say so in the description. A client
  whose configuration lives behind a login is `control: manual` with
  `observable: false` — a normal answer, not a gap.
* **A reviewable assertion for capabilities.** Listing two providers together
  claims either can satisfy the outcome. Say why you believe that.
* **No credentials**, in definitions, Kits, locks or tests.
* **Tests for behaviour that could corrupt configuration** or break an extension
  path. Existing tests must keep passing.

## What is out of scope for v0

`control: agent`, runtime health monitoring, automatic failover, provider version
pinning, capability contracts, conformance suites, a hosted or federated
registry, Kit discovery or marketplace, execution proxying, credential
management, and Secure MCP Tunnel support.

These are not rejected ideas; they are deliberately not part of v0. Proposals
that need the resolver to learn a new concept belong in an issue, not a pull
request.
