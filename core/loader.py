"""Load and validate Kits and the Git-native definitions catalogue."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parent.parent
ID = re.compile(r"^[a-z][a-z0-9-]*$")
DELIVERIES = {"local-mcp", "remote-mcp", "exec"}
CONTROLS = {"write", "manual", "none"}


class DefinitionError(ValueError):
    """Invalid Kit or catalogue definition."""


def _mapping(value: object, where: str) -> dict:
    if not isinstance(value, dict):
        raise DefinitionError(f"{where} must be a mapping")
    return value


def _id(value: object, where: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise DefinitionError(f"{where} must be a lowercase hyphenated id")
    return value


def _id_list(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise DefinitionError(f"{where} must be a list of ids")
    result = [_id(x, where) for x in value]
    if len(result) != len(set(result)):
        raise DefinitionError(f"{where} contains duplicates")
    return result


def read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DefinitionError(f"cannot read {path}: {exc}") from exc
    return _mapping(data, str(path))


def load_kit(path: Path) -> dict:
    kit = read_yaml(path)
    allowed = {"name", "targets", "tools", "capabilities"}
    extra = set(kit) - allowed
    if extra:
        raise DefinitionError(f"Kit has unsupported fields: {', '.join(sorted(extra))}")
    _id(kit.get("name"), "Kit name")
    targets = kit.get("targets")
    if not isinstance(targets, list) or not targets:
        raise DefinitionError("Kit targets must be a nonempty list")
    target_ids = []
    for target in targets:
        _mapping(target, "target")
        target_ids.append(_id(target.get("id"), "target id"))
        _id(target.get("client"), "target client")
        if set(target) != {"id", "client"}:
            raise DefinitionError("target may contain only id and client")
    if len(target_ids) != len(set(target_ids)):
        raise DefinitionError("Kit target ids must be unique")
    _id_list(kit.get("tools", []), "Kit tools")
    _id_list(kit.get("capabilities", []), "Kit capabilities")
    if set(kit.get("tools", [])) & set(kit.get("capabilities", [])):
        raise DefinitionError("tool and capability ids must not overlap in a Kit")
    return kit


def load_catalog(root: Path = ROOT) -> dict:
    catalog = {"clients": {}, "providers": {}, "capabilities": {}, "root": root}
    for kind in ("clients", "providers", "capabilities"):
        directory = root / kind
        if not directory.is_dir():
            raise DefinitionError(f"missing catalogue directory: {directory}")
        for path in sorted(directory.glob("*.yaml")):
            definition = read_yaml(path)
            identifier = _id(definition.get("id"), f"{path} id")
            if identifier != path.stem:
                raise DefinitionError(f"{path}: id must match filename")
            if identifier in catalog[kind]:
                raise DefinitionError(f"duplicate {kind} id: {identifier}")
            if kind == "providers":
                delivery = _mapping(definition.get("delivery"), f"{path} delivery")
                if not delivery or set(delivery) - DELIVERIES:
                    raise DefinitionError(f"{path}: invalid delivery mechanisms")
                for mode, payload in delivery.items():
                    _mapping(payload, f"{path} {mode}")
                    required = "url" if mode == "remote-mcp" else "command"
                    if not isinstance(payload.get(required), str) or not payload[required]:
                        raise DefinitionError(f"{path}: {mode} needs {required}")
            elif kind == "capabilities":
                providers = _id_list(definition.get("providers"), f"{path} providers")
                if not providers:
                    raise DefinitionError(f"{path}: provider list cannot be empty")
            else:
                consumes = _id_list(definition.get("consumes"), f"{path} consumes")
                if set(consumes) - DELIVERIES:
                    raise DefinitionError(f"{path}: invalid consumed mechanisms")
                if definition.get("control") not in CONTROLS:
                    raise DefinitionError(f"{path}: invalid control")
                if not isinstance(definition.get("observable"), bool):
                    raise DefinitionError(f"{path}: observable must be boolean")
                if definition["control"] == "write":
                    if not isinstance(definition.get("config"), str) or not definition["config"]:
                        raise DefinitionError(f"{path}: write client needs config path")
                    if not isinstance(definition.get("writer"), str):
                        raise DefinitionError(f"{path}: write client needs writer")
                if definition["control"] == "manual":
                    steps = definition.get("procedure")
                    if not isinstance(steps, list) or not steps or not all(isinstance(s, str) for s in steps):
                        raise DefinitionError(f"{path}: manual client needs procedure steps")
            catalog[kind][identifier] = definition
    return catalog


def definitions_commit(root: Path = ROOT) -> str:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "clients", "providers", "capabilities"],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DefinitionError("definitions require a Git commit") from exc
    if dirty:
        raise DefinitionError("definitions contain uncommitted changes; commit them before locking")
    return commit
