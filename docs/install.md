# Install OneKit with an agent

These are the complete installation instructions. You need Git, network access,
and Python 3.11 or newer with `venv` and `pip`. If a prerequisite is missing,
report it and stop. Do not substitute another installer.

The public URL of this document is
`https://raw.githubusercontent.com/365yoyo/onekit/main/docs/install.md`. Clone
the corresponding repository, `https://github.com/365yoyo/onekit.git`. From a
writable directory that does not already contain a directory named `onekit`,
run these commands in a POSIX shell. Check that `python3 --version` reports 3.11 or newer
before continuing; if it does not, use an installed Python 3.11+ executable
for the virtual environment or stop:

```sh
python3 --version
git clone https://github.com/365yoyo/onekit.git onekit
cd onekit
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/onekit probe
```

Report the `probe` output: which local client commands were found, the
catalogued control level for each client, and which account or workspace
access remains unverified. A ChatGPT workspace cannot be discovered through
this CLI. State any install or probe failure rather than claiming success.

Stop after `probe`. Do not run `setup`, edit client configuration, start OAuth,
or confirm manual steps during installation. `probe` is read-only and checks
whether a local client command is on `PATH`.
