"""Turn resolution rows into writer plans and exact manual procedures."""

from __future__ import annotations

import importlib
from pathlib import Path

from core.loader import DefinitionError


def config_path(client: dict) -> Path:
    return Path(client["config"]).expanduser()


def compile_rows(kit: dict, catalog: dict, rows: list[dict], force: bool = False) -> dict:
    result = {"plans": {}, "manual": [], "blocked": []}
    for target in kit["targets"]:
        target_id = target["id"]
        client = catalog["clients"][target["client"]]
        target_rows = [row for row in rows if row["target"] == target_id]
        if client["control"] == "write":
            try:
                writer = importlib.import_module("writers." + client["writer"].replace("-", "_"))
            except ModuleNotFoundError as exc:
                raise DefinitionError(f"unknown writer: {client['writer']}")
            entries = {}
            for row in target_rows:
                if row["control"] != "write":
                    continue
                payload = catalog["providers"][row["provider"]]["delivery"][row["delivery"]]
                if row["provider"] in entries and entries[row["provider"]] != payload:
                    raise DefinitionError(f"conflicting payloads for {row['provider']}")
                entries[row["provider"]] = payload
            plan = writer.plan(config_path(client), kit["name"], target_id, entries, force=force)
            plan["writer"] = writer
            result["plans"][target_id] = plan
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
                    raise DefinitionError(f"manual procedure missing value: {exc}") from exc
                result["manual"].append({**row, "steps": steps})
    return result
