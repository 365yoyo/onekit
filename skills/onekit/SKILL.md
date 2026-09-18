# OneKit agent workflow

On installation, follow `docs/install.md`, run `onekit probe`, report findings,
and stop before setup.

When the person asks to configure a Kit, run `resolve` and show `diff` before
`setup`. Mention provider substitutions once and give manual procedures
verbatim. Report every blocked item. Never edit a Kit to work around a gap,
never run `--force` unprompted, and never confirm a manual step the person did
not say they completed. Never enable a target the person did not list.
