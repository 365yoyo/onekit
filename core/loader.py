"""Load and validate Kits and the definitions catalogue."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import re
import subprocess

import yaml


CATALOGUE_KINDS = ("clients", "providers", "capabilities")


def default_root() -> Path:
    """Locate the definitions catalogue for a checkout or an installed package.

    In a source checkout the catalogue sits beside ``core/``. An installed
    distribution ships the same YAML as package data inside ``core/``. Neither
    location depends on the current working directory.
    """
    package = Path(__file__).resolve().parent
    checkout = package.parent
    if all((checkout / kind).is_dir() for kind in CATALOGUE_KINDS):
        return checkout
    return package


ROOT = default_root()
ID = re.compile(r"^[a-z][a-z0-9-]*$")
DELIVERIES = {"local-mcp", "remote-mcp", "exec"}
CONTROLS = {"write", "manual", "none"}
# Top-level lock keys OneKit owns. A Kit item sharing one of these names would
# overwrite lock metadata, so it is rejected at validation time.
RESERVED_LOCK_KEYS = {"lock_version", "definitions", "accepted_at"}


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
    items = set(kit.get("tools", [])) | set(kit.get("capabilities", []))
    if set(kit.get("tools", [])) & set(kit.get("capabilities", [])):
        raise DefinitionError("tool and capability ids must not overlap in a Kit")
    reserved = sorted(items & RESERVED_LOCK_KEYS)
    if reserved:
        raise DefinitionError(
            f"Kit item ids reserved by the lock file: {', '.join(reserved)}"
        )
    return kit


def load_catalog(root: Path = None) -> dict:
    root = ROOT if root is None else root
    catalog = {"clients": {}, "providers": {}, "capabilities": {}, "root": root}
    for kind in CATALOGUE_KINDS:
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
                    _id(definition.get("writer"), f"{path} writer")
                if definition["control"] == "manual":
                    steps = definition.get("procedure")
                    if not isinstance(steps, list) or not steps or not all(isinstance(s, str) for s in steps):
                        raise DefinitionError(f"{path}: manual client needs procedure steps")
            catalog[kind][identifier] = definition
    return catalog


def _git_commit(root: Path) -> str | None:
    """Return the commit pinning this catalogue, or None if it is not in Git.

    The catalogue directories must belong to the repository found from ``root``;
    otherwise an unrelated enclosing repository (an installed package inside a
    checkout, say) would supply a meaningless commit.
    """
    def run(*arguments: str) -> str:
        return subprocess.run(["git", *arguments], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()
    try:
        toplevel = Path(run("rev-parse", "--show-toplevel")).resolve()
        if not all((toplevel / kind).resolve() == (root / kind).resolve()
                   for kind in CATALOGUE_KINDS):
            return None
        commit = run("rev-parse", "HEAD")
        dirty = run("status", "--porcelain", "--", *CATALOGUE_KINDS)
    except (OSError, subprocess.CalledProcessError):
        return None
    if dirty:
        raise DefinitionError(
            "definitions contain uncommitted changes; commit them before locking"
        )
    return commit


def definitions_provenance(root: Path = None) -> str:
    """Identify the definitions used to resolve a Kit.

    A Git checkout pins an exact commit. An installed release has no repository,
    so the distribution version identifies its bundled catalogue instead.
    """
    root = ROOT if root is None else root
    commit = _git_commit(root)
    if commit:
        return f"onekit-defs@{commit}"
    try:
        return f"onekit-defs@v{metadata.version('onekit')}"
    except metadata.PackageNotFoundError as exc:
        raise DefinitionError(
            "cannot identify the definitions: not a Git checkout and OneKit is "
            "not installed as a package"
        ) from exc
