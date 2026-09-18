# Adding support to OneKit

Most contributions are one YAML file and no code. This page covers all four
cases, in increasing order of effort. See [concepts](concepts.md) for the ideas
behind them.

```text
add a provider    → 1 yaml file                      → 0 core changes
add a capability  → 1 yaml file                      → 0 core changes
add a client      → 1 yaml file + an existing writer → 0 core changes
                    (a new writer if no format fits)
```

The design invariant is that novel integration mechanics **extend** OneKit rather
than modify its core. If something you want requires the resolver to learn a new
concept, it is out of scope for v0 — say so in the issue rather than working
around it.

## Add a provider

One file, `providers/<id>.yaml`. The `id` must match the filename.

```yaml
id: another-search
delivery:
  remote-mcp:
    url: https://example.org/mcp
```

A provider may declare more than one delivery mechanism; the resolver picks the
first one the target client consumes, in declaration order.

* `local-mcp` and `exec` take `command` and optional string `args`.
* `remote-mcp` takes `url`, which must be a real direct HTTPS MCP endpoint — not
  a tunnel id, and not a redirect to a UI.
* Never put credentials in a definition. Clients authenticate to providers
  directly.

## Add a capability

One file, `capabilities/<id>.yaml`.

```yaml
id: another-web-search
providers: [another-search, search-b]
```

Order is selection order. OneKit does not prove two providers interchangeable —
putting them in one list is a reviewable assertion that either can satisfy the
requested outcome, and the pull request is where that gets checked. Tool names
and schemas may still differ between them.

## Add a client

One file, `clients/<id>.yaml`, with `id`, `consumes`, `control` and `observable`.

```yaml
id: example-client
consumes: [local-mcp, remote-mcp]
control: write
observable: true
config: ~/.example-client/config.toml
writer: toml-region
probe_command: example-client
```

* A `write` client also needs `config` and an existing `writer`.
* A `manual` client instead needs `procedure` steps. Steps are templates filled
  from the resolved provider: `{name}`, `{item}`, `{target}` and the provider's
  own payload keys, such as `{url}` or `{command}`. Write a literal brace as
  `{{` or `}}`.
* `probe_command` is optional. When present, `onekit probe` reports whether that
  command is on `PATH`; when absent, probe says the client's access could not be
  verified programmatically.

Before contributing a client, verify its configuration path, format, control
level, readback behaviour and any policy or authentication requirements against
the real client, and include that evidence in the pull request. A client whose
configuration lives behind a login is `control: manual` with
`observable: false` — that is a normal, supported answer, not a gap.

## Add a writer

Only needed when a client stores configuration in a format no existing writer
handles. A writer owns one format; Core never imports one directly.

Create `writers/<name>_region.py` (referenced from a client as
`writer: <name>-region`) and implement four things:

```python
from writers import WriterError

SUPPORTED_DELIVERIES = frozenset({"local-mcp", "remote-mcp"})


def plan(path, kit, targets, entries, force=False):
    """Read the current state and return a plan. Must not mutate anything."""


def apply(plan_data):
    """Commit the plan, refusing if the file changed since plan() ran."""


def revert(plan_data):
    """Restore the pre-apply state, so a failed multi-file setup can roll back."""
```

* `entries` maps a provider id to `{"delivery": str, "payload": dict}`. The
  compiler rejects any delivery not in your `SUPPORTED_DELIVERIES` before
  anything is written, so you never receive one you cannot express. Raise
  `WriterError` if you are handed one anyway.
* The plan you return must include `path` and `changes` — a list of
  `(symbol, name)` pairs using `+`, `-` and `~` — plus whatever private state
  `apply` and `revert` need. `onekit diff` renders `changes`.
* **Raise `WriterError` for every failure.** It is the writer-neutral error
  contract: the CLI renders it as a clean `onekit: ...` message. Do not import
  from another writer, and do not raise that writer's error class.
* `revert` must refuse rather than clobber if the target changed after `apply`
  committed. Rolling back over someone else's edit is worse than stopping.

Your writer must honour the four managed-region rules in
[concepts](concepts.md#managed-regions): touch only what OneKit owns, verify
ownership and integrity before replacing, remove entries that leave the Kit, and
create a boundary without claiming existing content.

`writers/toml_region.py` is the reference implementation.

## Checking your contribution

```sh
python -m unittest discover -s tests
onekit --kit examples/kit.yaml resolve
onekit --kit examples/kit.yaml diff
```

`resolve` and `diff` are read-only, so they are safe to run against your own
machine. Run `diff` before `setup`, always.

Set `ONEKIT_SKIP_INSTALL_TEST=1` to skip the packaging test, which builds a
virtual environment and needs a package index.

If you added a writer, add a test for it. `tests/fixtures.py` has a
`fake_writer` helper that loads a writer module from a temporary file through the
same import path a real one uses.
