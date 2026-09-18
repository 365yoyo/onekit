# Contributing definitions

OneKit's current catalogue is in `providers/`, `capabilities/`, and `clients/`.
Add a provider as one YAML file named `providers/<id>.yaml`:

```yaml
id: another-search
delivery:
  remote-mcp:
    url: https://example.org/mcp
```

Allowed delivery keys in v0 are `local-mcp`, `remote-mcp`, and `exec`.
`local-mcp` and `exec` provide `command` and optional string `args`;
`remote-mcp` provides `url`. The endpoint must be a real direct HTTP MCP
endpoint, not a tunnel ID. Do not put credentials in a definition.

Add a capability as one YAML file named `capabilities/<id>.yaml`:

```yaml
id: another-web-search
providers: [another-search, search-b]
```

The list order determines selection. Providers in a capability should
credibly deliver the same outcome; review that assertion. A Kit requests
the capability ID, and the resolver selects the first provider with a delivery
mechanism the target client consumes. No Core change is needed. Run
`python -m unittest discover -s tests -v` and `onekit --kit ... resolve`.

To add a client, create one YAML file with `id`, `consumes`, `control`, and
`observable`. A `write` client also needs `config` and an existing `writer`;
a `manual` client needs `procedure` steps with placeholders from its provider
payload. A new storage format needs a writer module, not a resolver change.
Before contributing a client, verify its configuration path, format, control,
readback behavior, and policy or authentication requirements with the real
client. Include that evidence in the pull request.
