"""OneKit v0 command line interface."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sys
import tempfile

import yaml

from core.compiler import apply_plans, compile_rows, pending_plans
from core.doctor import report as doctor_report
from core.loader import (
    CONTROLS, RESERVED_LOCK_KEYS, ROOT, DefinitionError, definitions_provenance,
    load_catalog, load_kit,
)
from core.resolver import resolve
from writers import WriterError


SETUP_STATES = {"pending", "complete", "asserted"}


def _validate_lock(lock: dict, where: str) -> dict:
    """Reject a malformed lock before it is trusted or written."""
    if not isinstance(lock, dict) or lock.get("lock_version") != 1:
        raise DefinitionError(f"{where} is not a OneKit v1 lock")
    for key in ("definitions", "accepted_at"):
        if not isinstance(lock.get(key), str) or not lock[key]:
            raise DefinitionError(f"{where} is missing a valid '{key}'")
    for item, entry in lock.items():
        if item in RESERVED_LOCK_KEYS:
            continue
        if not isinstance(entry, dict):
            raise DefinitionError(f"{where}: item '{item}' must be a mapping")
        requested = entry.get("requested")
        if not isinstance(requested, list) or not all(isinstance(x, str) for x in requested):
            raise DefinitionError(f"{where}: item '{item}' needs a 'requested' list")
        for target, state in entry.items():
            if target == "requested":
                continue
            if not isinstance(state, dict):
                raise DefinitionError(f"{where}: '{item}/{target}' must be a mapping")
            for field in ("provider", "delivery", "control", "setup"):
                if field not in state:
                    raise DefinitionError(f"{where}: '{item}/{target}' is missing '{field}'")
            if state["control"] not in CONTROLS:
                raise DefinitionError(f"{where}: '{item}/{target}' has an invalid control")
            if state["setup"] not in SETUP_STATES:
                raise DefinitionError(f"{where}: '{item}/{target}' has an invalid setup state")
    return lock


def _lock(path: Path) -> dict:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _validate_lock(value, f"lock {path}")


def _save_lock(path: Path, lock: dict) -> None:
    # The lock is OneKit-owned, validated before it is written, and replaced
    # atomically so a crash cannot leave a half-written lock behind.
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=".onekit-lock-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(yaml.safe_dump(lock, sort_keys=False))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _make_lock(rows: list[dict], old: dict, provenance: str) -> dict:
    lock = {"lock_version": 1, "definitions": provenance,
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


def _show_changes(compiled: dict) -> None:
    """Render the proposed changes. Makes no claim about what was written."""
    for plan in compiled["plans"].values():
        print(f"{', '.join(plan['targets'])}  {plan['path']}")
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


def _require_force_consent() -> None:
    """Ask for destructive consent, after the destructive diff has been shown."""
    if not sys.stdin.isatty():
        raise DefinitionError("--force requires interactive human confirmation")
    answer = input("Destructive replacement requested. Type FORCE to continue: ")
    if answer != "FORCE":
        raise DefinitionError("force not confirmed")


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
            _validate_lock(lock, f"lock {lock_path}")
            _save_lock(lock_path, lock)
            print(f"{args.target} / {args.item}: {state['setup']}")
            return 0

        # diff and setup share one validate-then-plan phase. Nothing below
        # mutates anything until every plan and the new lock have been built.
        force = args.command == "setup" and args.force
        compiled = compile_rows(kit, catalog, rows, force=force)
        if args.command == "diff":
            _show_changes(compiled)
            print("Nothing written.")
            return 0
        provenance = definitions_provenance(args.definitions)
        new_lock = _validate_lock(_make_lock(rows, _lock(lock_path), provenance),
                                  "generated lock")
        planned = pending_plans(compiled)
        _show_changes(compiled)
        if force:
            _require_force_consent()
        applied = apply_plans(planned)
        _save_lock(lock_path, new_lock)
        if applied:
            total = sum(len(plan["changes"]) for plan in applied)
            print(f"Applied {total} entry change(s) in {len(applied)} file(s):")
            for plan in applied:
                print(f"  {plan['path']}")
        else:
            print("No configuration changes were required.")
        print(f"Lock written: {lock_path}")
        return 0
    except (DefinitionError, WriterError, OSError, yaml.YAMLError) as exc:
        print(f"onekit: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
