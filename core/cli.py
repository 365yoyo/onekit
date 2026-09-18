"""OneKit v0 command line interface."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys

import yaml

from core.compiler import compile_rows
from core.doctor import report as doctor_report
from core.loader import ROOT, DefinitionError, definitions_commit, load_catalog, load_kit
from core.resolver import resolve
from writers.toml_region import RegionError


def _lock(path: Path) -> dict:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("lock_version") != 1:
        raise DefinitionError("existing lock is not a OneKit v1 lock")
    return value


def _save_lock(path: Path, lock: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Lock is OneKit-owned and is replaced only after validating its schema.
    path.write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")


def _make_lock(rows: list[dict], old: dict, commit: str) -> dict:
    lock = {"lock_version": 1, "definitions": f"onekit-defs@{commit}",
            "accepted_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
    for row in rows:
        item = lock.setdefault(row["item"], {"requested": row["requested"]})
        previous = old.get(row["item"], {}).get(row["target"], {})
        setup = "pending"
        if row["control"] == "write":
            setup = "complete"
        elif row["control"] == "manual" and all(previous.get(key) == row[key] for key in ("provider", "delivery", "control")):
            setup = previous.get("setup", "pending")
        item[row["target"]] = {"provider": row["provider"], "delivery": row["delivery"],
                               "control": row["control"], "setup": setup}
    return lock


def _show_resolution(rows: list[dict]) -> None:
    for row in rows:
        selection = f"{row['provider']} ({row['delivery']}, {row['control']})" if row["provider"] else "blocked"
        print(f"{row['target']} / {row['item']}: {selection}; {row['reason']}")


def _show_diff(compiled: dict) -> None:
    for target, plan in compiled["plans"].items():
        print(f"{target}  {plan['path']}")
        for symbol, name in plan["changes"]:
            print(f"  {symbol} {name}")
        if not plan["changes"]:
            print("  no entry changes")
    for item in compiled["manual"]:
        print(f"{item['target']} / {item['item']}: manual procedure")
        for index, step in enumerate(item["steps"], 1):
            print(f"  {index}. {step}")
    for item in compiled["blocked"]:
        print(f"{item['target']} / {item['item']}: blocked; {item['reason']}")
    print("Nothing written.")


def _show_doctor(result: dict) -> None:
    for row in result["rows"]:
        if row["control"] == "none":
            state = "blocked"
        elif row["setup"] == "asserted":
            state = "configured (asserted)"
        elif row["setup"] == "complete":
            state = "configured"
        else:
            state = f"{row['control']}/pending"
        provider = row["provider"] or "none"
        print(f"{row['item']} / {row['target']}: {state}; provider: {provider}")
    for target, count in result["counts"].items():
        total = sum(row["target"] == target for row in result["rows"])
        print(f"{target}: {count['available']}/{total} available, {count['configured']} configured")
    for drift in result["drift"]:
        print(f"\n{drift['item'].replace('-', ' ').title()} / {drift['target']}\n")
        print(f"  locked:       {drift['locked']}")
        print(f"  observed:     {drift['observed']}")
        print(f"  next in list: {drift['next'] or 'none'}")
        print("\n  Note: tool names and schemas differ.\n")
        print("  No change made.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onekit")
    parser.add_argument("--kit", type=Path, default=Path("kit.yaml"))
    parser.add_argument("--definitions", type=Path, default=ROOT)
    parser.add_argument("--lock", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("probe", "resolve", "diff", "doctor", "setup", "confirm"):
        command = sub.add_parser(name)
        if name == "doctor":
            command.add_argument("--fixture", choices=["search-a-down"])
        if name == "setup":
            command.add_argument("--force", action="store_true")
        if name == "confirm":
            command.add_argument("target")
            command.add_argument("item")
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(args.definitions)
        if args.command == "probe":
            for client_id, client in catalog["clients"].items():
                executable = client.get("probe_command")
                if executable:
                    found = shutil.which(executable)
                    state = f"installed ({found})" if found else "not found in PATH"
                else:
                    state = "account access not programmatically verified"
                print(f"{client_id}: {state}; control={client['control']}; observable={str(client['observable']).lower()}; consumes={','.join(client['consumes'])}")
            return 0
        kit = load_kit(args.kit)
        rows = resolve(kit, catalog)
        lock_path = args.lock or args.kit.with_suffix(".lock.yaml")
        if args.command == "resolve":
            _show_resolution(rows)
            return 0
        if args.command == "doctor":
            _show_doctor(doctor_report(rows, _lock(lock_path), catalog, args.fixture))
            return 0
        if args.command == "confirm":
            row = next((r for r in rows if r["target"] == args.target and r["item"] == args.item), None)
            if row is None or row["control"] != "manual":
                raise DefinitionError("confirm requires a resolved manual item and target")
            lock = _lock(lock_path)
            state = lock.get(args.item, {}).get(args.target, {})
            if any(state.get(key) != row[key] for key in ("provider", "delivery", "control")):
                raise DefinitionError("lock decision does not match current resolution; run setup first")
            state["setup"] = "asserted" if not catalog["clients"][row["client"]]["observable"] else "complete"
            state["confirmed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            _save_lock(lock_path, lock)
            print(f"{args.target} / {args.item}: {state['setup']}")
            return 0
        if args.command == "setup" and args.force:
            if not sys.stdin.isatty():
                raise DefinitionError("--force requires interactive human confirmation")
            answer = input("Destructive replacement requested. Type FORCE to continue: ")
            if answer != "FORCE":
                raise DefinitionError("force not confirmed")
        compiled = compile_rows(kit, catalog, rows, force=args.command == "setup" and args.force)
        _show_diff(compiled)
        if args.command == "diff":
            return 0
        commit = definitions_commit(args.definitions)
        old = _lock(lock_path)
        for plan in compiled["plans"].values():
            plan["writer"].apply(plan)
        _save_lock(lock_path, _make_lock(rows, old, commit))
        print(f"Lock written: {lock_path}")
        return 0
    except (DefinitionError, RegionError, OSError, yaml.YAMLError) as exc:
        print(f"onekit: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
