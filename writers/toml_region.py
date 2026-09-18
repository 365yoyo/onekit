"""Codex-compatible TOML managed region. Unmanaged bytes are preserved."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib

from writers import WriterError

# Delivery mechanisms this writer can express in Codex's TOML schema. Anything
# else is rejected by the compiler before a byte is written.
SUPPORTED_DELIVERIES = frozenset({"local-mcp", "remote-mcp"})


class RegionError(WriterError):
    """The existing configuration cannot be changed safely."""


START = re.compile(
    r"(?m)^# >>> onekit kit=([a-z0-9-]+) "
    r"target=([a-z0-9-]+(?:,[a-z0-9-]+)*) sha256=([0-9a-f]{64})\n"
)
END = "# <<< onekit\n"


def _string(value: str) -> str:
    # JSON string escaping is also valid for TOML basic strings.
    return json.dumps(value, ensure_ascii=False)


def _render(name: str, payload: dict) -> str:
    lines = [f"[mcp_servers.{_string(name)}]\n"]
    for key, value in payload.items():
        if isinstance(value, str):
            rendered = _string(value)
        elif isinstance(value, list) and all(isinstance(x, str) for x in value):
            rendered = "[" + ", ".join(_string(x) for x in value) + "]"
        else:
            raise RegionError(f"unsupported TOML value for {name}.{key}")
        lines.append(f"{key} = {rendered}\n")
    lines.append("\n")
    return "".join(lines)


def _body(entries: dict[str, dict]) -> str:
    lines = []
    for name, entry in sorted(entries.items()):
        delivery = entry["delivery"]
        if delivery not in SUPPORTED_DELIVERIES:
            raise RegionError(
                f"toml-region cannot express delivery '{delivery}' for '{name}'"
            )
        lines.append(_render(name, entry["payload"]))
    return "".join(lines)


def plan(path: Path, kit: str, targets: list[str], entries: dict[str, dict],
         force: bool = False) -> dict:
    targets = sorted(targets)
    if path.is_symlink():
        raise RegionError(f"refusing symlinked TOML configuration: {path}")
    existed = path.exists()
    original = path.read_text(encoding="utf-8") if existed else ""
    starts = list(START.finditer(original))
    if len(starts) > 1 or original.count(END) != len(starts):
        raise RegionError(f"ambiguous OneKit TOML ownership boundary in {path}")
    if starts:
        match = starts[0]
        if match.group(1) != kit:
            raise RegionError(
                f"OneKit TOML region in {path} belongs to Kit '{match.group(1)}', not '{kit}'"
            )
        end_at = original.find(END, match.end())
        if end_at < 0:
            raise RegionError(f"OneKit TOML region in {path} has no end marker")
        old_body = original[match.end():end_at]
        if hashlib.sha256(old_body.encode()).hexdigest() != match.group(3) and not force:
            raise RegionError(f"OneKit TOML region integrity mismatch in {path}")
        before, after = original[:match.start()], original[end_at + len(END):]
        old = tomllib.loads(old_body).get("mcp_servers", {}) if old_body.strip() else {}
    else:
        before, after, old = original, "", {}
    try:
        unmanaged = tomllib.loads(before + after) if (before + after).strip() else {}
    except tomllib.TOMLDecodeError as exc:
        raise RegionError(f"unmanaged TOML in {path} is invalid: {exc}") from exc
    occupied = unmanaged.get("mcp_servers", {})
    if not isinstance(occupied, dict) or set(occupied) & set(entries):
        raise RegionError(
            f"managed entry would overlap unmanaged MCP configuration in {path}"
        )
    body = _body(entries)
    if body:
        prefix = before if not before or before.endswith("\n") else before + "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        marker = (f"# >>> onekit kit={kit} target={','.join(targets)} "
                  f"sha256={hashlib.sha256(body.encode()).hexdigest()}\n")
        proposed = prefix + marker + body + END + after
    else:
        proposed = before + after
    try:
        tomllib.loads(proposed)
    except tomllib.TOMLDecodeError as exc:
        raise RegionError(f"proposed TOML for {path} is invalid: {exc}") from exc
    new = {name: entry["payload"] for name, entry in entries.items()}
    changes = [(symbol, name) for symbol, names in (
        ("+", set(new) - set(old)), ("-", set(old) - set(new)),
        ("~", {name for name in set(old) & set(new) if old[name] != new[name]}),
    ) for name in sorted(names)]
    return {"path": path, "existed": existed, "original": original,
            "proposed": proposed, "changes": changes, "targets": targets}


@contextmanager
def _locked(path: Path):
    """Hold an exclusive lock for this configuration file.

    The sidecar is a durable lock file, not a temporary artefact: unlinking it
    while another process may already hold a descriptor on it would silently
    break mutual exclusion. See docs/concepts.md.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".onekit.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _write(path: Path, text: str) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=".onekit-", dir=path.parent)
    try:
        if path.exists():
            os.fchmod(descriptor, path.stat().st_mode & 0o777)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def apply(plan_data: dict) -> None:
    path = plan_data["path"]
    if path.is_symlink():
        raise RegionError(f"refusing symlinked TOML configuration: {path}")
    with _locked(path):
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != plan_data["original"]:
            raise RegionError(f"{path} changed after diff; rerun diff")
        if current == plan_data["proposed"]:
            return
        _write(path, plan_data["proposed"])


def revert(plan_data: dict) -> None:
    """Undo a committed apply, refusing if the file moved on since."""
    path = plan_data["path"]
    if path.is_symlink():
        raise RegionError(f"refusing symlinked TOML configuration: {path}")
    with _locked(path):
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current == plan_data["original"]:
            return
        if current != plan_data["proposed"]:
            raise RegionError(
                f"{path} changed after it was written; not reverting over the change"
            )
        if plan_data["existed"]:
            _write(path, plan_data["original"])
        else:
            path.unlink(missing_ok=True)
