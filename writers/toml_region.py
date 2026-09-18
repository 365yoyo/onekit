"""Codex-compatible TOML managed region. Unmanaged bytes are preserved."""

from __future__ import annotations

import hashlib
import fcntl
import os
from pathlib import Path
import re
import tempfile
import tomllib


class RegionError(ValueError):
    """The existing configuration cannot be changed safely."""


START = re.compile(r"(?m)^# >>> onekit kit=([a-z0-9-]+) target=([a-z0-9-]+) sha256=([0-9a-f]{64})\n")
END = "# <<< onekit\n"


def _string(value: str) -> str:
    # JSON string escaping is also valid for TOML basic strings.
    import json
    return json.dumps(value, ensure_ascii=False)


def _body(entries: dict[str, dict]) -> str:
    lines = []
    for name, payload in sorted(entries.items()):
        lines.append(f"[mcp_servers.{_string(name)}]\n")
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


def plan(path: Path, kit: str, target: str, entries: dict[str, dict], force: bool = False) -> dict:
    if path.is_symlink():
        raise RegionError("refusing symlinked TOML configuration")
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    starts = list(START.finditer(original))
    if len(starts) > 1 or original.count(END) != len(starts):
        raise RegionError("ambiguous OneKit TOML ownership boundary")
    if starts:
        match = starts[0]
        if (match.group(1), match.group(2)) != (kit, target):
            raise RegionError("OneKit TOML region belongs to another Kit or target")
        end_at = original.find(END, match.end())
        if end_at < 0:
            raise RegionError("OneKit TOML region has no end marker")
        old_body = original[match.end():end_at]
        if hashlib.sha256(old_body.encode()).hexdigest() != match.group(3) and not force:
            raise RegionError("OneKit TOML region integrity mismatch")
        before, after = original[:match.start()], original[end_at + len(END):]
        old = tomllib.loads(old_body).get("mcp_servers", {}) if old_body.strip() else {}
    else:
        before, after, old = original, "", {}
    try:
        unmanaged = tomllib.loads(before + after) if (before + after).strip() else {}
    except tomllib.TOMLDecodeError as exc:
        raise RegionError(f"unmanaged TOML is invalid: {exc}") from exc
    occupied = unmanaged.get("mcp_servers", {})
    if not isinstance(occupied, dict) or set(occupied) & set(entries):
        raise RegionError("managed entry would overlap unmanaged MCP configuration")
    body = _body(entries)
    if body:
        prefix = before if not before or before.endswith("\n") else before + "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        marker = f"# >>> onekit kit={kit} target={target} sha256={hashlib.sha256(body.encode()).hexdigest()}\n"
        proposed = prefix + marker + body + END + after
    else:
        proposed = before + after
    try:
        tomllib.loads(proposed)
    except tomllib.TOMLDecodeError as exc:
        raise RegionError(f"proposed TOML is invalid: {exc}") from exc
    changes = [(symbol, name) for symbol, names in (
        ("+", set(entries) - set(old)), ("-", set(old) - set(entries)),
        ("~", {name for name in set(old) & set(entries) if old[name] != entries[name]}),
    ) for name in sorted(names)]
    return {"path": path, "original": original, "proposed": proposed, "changes": changes}


def apply(plan_data: dict) -> None:
    path = plan_data["path"]
    if path.is_symlink():
        raise RegionError("refusing symlinked TOML configuration")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".onekit.lock")
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != plan_data["original"]:
            raise RegionError("configuration changed after diff; rerun diff")
        if current == plan_data["proposed"]:
            return
        descriptor, temp_name = tempfile.mkstemp(prefix=".onekit-", dir=path.parent)
        try:
            if path.exists():
                os.fchmod(descriptor, path.stat().st_mode & 0o777)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(plan_data["proposed"])
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
