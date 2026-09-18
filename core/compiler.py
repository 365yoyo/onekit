"""Turn resolution rows into writer plans and exact manual procedures."""

from __future__ import annotations

import importlib
from pathlib import Path

from core.loader import ID, DefinitionError
from writers import WriterError


REQUIRED_WRITER_ATTRS = ("plan", "apply", "revert")


def config_path(client: dict) -> Path:
    return Path(client["config"]).expanduser()


def load_writer(name: str) -> object:
    """Load a writer by name and check it satisfies the writer contract."""
    if not isinstance(name, str) or not ID.fullmatch(name):
        raise DefinitionError(f"invalid writer name: {name!r}")
    try:
        module = importlib.import_module("writers." + name.replace("-", "_"))
    except ModuleNotFoundError as exc:
        raise DefinitionError(f"unknown writer: {name}") from exc
    missing = [attribute for attribute in REQUIRED_WRITER_ATTRS
               if not callable(getattr(module, attribute, None))]
    if missing:
        raise DefinitionError(
            f"writer {name} does not implement: {', '.join(missing)}"
        )
    return module


def compile_rows(kit: dict, catalog: dict, rows: list[dict], force: bool = False) -> dict:
    """Plan every mutation and procedure without touching the filesystem.

    Targets sharing one physical configuration file are planned together, so a
    plan is never calculated against a copy of a file another plan will change.
    """
    result = {"plans": {}, "manual": [], "blocked": []}
    groups: dict[str, dict] = {}
    for target in kit["targets"]:
        target_id = target["id"]
        client = catalog["clients"][target["client"]]
        target_rows = [row for row in rows if row["target"] == target_id]
        if client["control"] == "write":
            writer = load_writer(client["writer"])
            path = config_path(client)
            key = str(path.resolve())
            group = groups.setdefault(key, {
                "path": path, "writer": writer, "writer_name": client["writer"],
                "targets": [], "entries": {}, "sources": {},
            })
            if group["writer_name"] != client["writer"]:
                raise DefinitionError(
                    f"targets '{group['targets'][0]}' and '{target_id}' share "
                    f"{path} but declare different writers "
                    f"({group['writer_name']} and {client['writer']})"
                )
            group["targets"].append(target_id)
            supported = getattr(writer, "SUPPORTED_DELIVERIES", frozenset())
            for row in target_rows:
                if row["control"] != "write":
                    continue
                if row["delivery"] not in supported:
                    raise DefinitionError(
                        f"writer '{client['writer']}' cannot express delivery "
                        f"'{row['delivery']}' required by '{row['provider']}' "
                        f"for target '{target_id}'"
                    )
                payload = catalog["providers"][row["provider"]]["delivery"][row["delivery"]]
                entry = {"delivery": row["delivery"], "payload": payload}
                previous = group["entries"].get(row["provider"])
                if previous is not None and previous != entry:
                    raise DefinitionError(
                        f"targets '{group['sources'][row['provider']]}' and "
                        f"'{target_id}' share {path} but need different "
                        f"configuration for provider '{row['provider']}'"
                    )
                group["entries"][row["provider"]] = entry
                group["sources"].setdefault(row["provider"], target_id)
        for row in target_rows:
            if row["control"] == "none":
                result["blocked"].append(row)
            elif row["control"] == "manual":
                payload = catalog["providers"][row["provider"]]["delivery"][row["delivery"]]
                values = {"name": row["provider"], "item": row["item"],
                          "target": target_id, **payload}
                try:
                    steps = [step.format_map(values) for step in client["procedure"]]
                except KeyError as exc:
                    raise DefinitionError(
                        f"manual procedure for client '{target['client']}' uses "
                        f"unknown value {exc}; escape a literal brace as {{{{ or }}}}"
                    ) from exc
                except (IndexError, ValueError) as exc:
                    raise DefinitionError(
                        f"manual procedure for client '{target['client']}' is not "
                        f"a valid template: {exc}"
                    ) from exc
                result["manual"].append({**row, "steps": steps})
    for key, group in groups.items():
        plan = group["writer"].plan(group["path"], kit["name"],
                                    group["targets"], group["entries"], force=force)
        plan["writer"] = group["writer"]
        plan["targets"] = sorted(group["targets"])
        result["plans"][key] = plan
    return result


def pending_plans(compiled: dict) -> list[dict]:
    """Plans that would actually change a file."""
    return [plan for plan in compiled["plans"].values()
            if plan.get("original") != plan.get("proposed") or plan["changes"]]


def apply_plans(plans: list[dict]) -> list[dict]:
    """Apply plans as one unit, rolling back everything if any plan fails."""
    applied: list[dict] = []
    try:
        for plan in plans:
            plan["writer"].apply(plan)
            applied.append(plan)
    except BaseException as exc:
        unrecovered = []
        for plan in reversed(applied):
            try:
                plan["writer"].revert(plan)
            except Exception as revert_error:
                unrecovered.append(f"{plan['path']} ({revert_error})")
        if unrecovered:
            raise WriterError(
                f"{exc}. Rollback could not restore: {'; '.join(unrecovered)}. "
                f"These files need manual recovery."
            ) from exc
        if applied:
            raise WriterError(
                f"{exc}. Rolled back {len(applied)} earlier file(s); "
                f"no changes remain applied."
            ) from exc
        raise
    return applied
