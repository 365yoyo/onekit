"""Report configured state and the v0 drift fixture without runtime checks."""

from __future__ import annotations


def report(rows: list[dict], lock: dict | None, catalog: dict, fixture: str | None = None) -> dict:
    status = []
    counts = {}
    for row in rows:
        target = row["target"]
        counts.setdefault(target, {"available": 0, "configured": 0})
        available = row["control"] != "none"
        if available:
            counts[target]["available"] += 1
        locked = (lock or {}).get(row["item"], {}).get(target, {})
        setup = locked.get("setup", "pending")
        if locked.get("provider") != row["provider"] or locked.get("delivery") != row["delivery"]:
            setup = "pending"
        if setup in ("complete", "asserted"):
            counts[target]["configured"] += 1
        status.append({**row, "setup": setup})
    drift = []
    if fixture == "search-a-down":
        for row in status:
            locked = (lock or {}).get(row["item"], {}).get(row["target"], {})
            if locked.get("provider") != "search-a":
                continue
            choices = row["requested"]
            client = catalog["clients"][row["client"]]
            following = choices[choices.index("search-a") + 1:]
            next_provider = next((provider_id for provider_id in following
                if provider_id in catalog["providers"] and
                any(mode in client["consumes"] for mode in catalog["providers"][provider_id]["delivery"])), None)
            drift.append({"target": row["target"], "item": row["item"],
                          "locked": "search-a", "observed": "failing (fixture)",
                          "next": next_provider, "note": "tool names and schemas differ"})
    return {"rows": status, "counts": counts, "drift": drift}
