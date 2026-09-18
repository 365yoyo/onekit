"""Pure per-target resolution of tools and ordered capabilities."""

from __future__ import annotations

from core.loader import DefinitionError


def resolve(kit: dict, catalog: dict) -> list[dict]:
    results = []
    for target in kit["targets"]:
        client_id = target["client"]
        client = catalog["clients"].get(client_id)
        if client is None:
            raise DefinitionError(f"unknown client: {client_id}")
        for kind, requested in (("tool", kit.get("tools", [])),
                                ("capability", kit.get("capabilities", []))):
            for item_id in requested:
                if kind == "tool":
                    choices = [item_id]
                else:
                    capability = catalog["capabilities"].get(item_id)
                    if capability is None:
                        raise DefinitionError(f"unknown capability: {item_id}")
                    choices = capability["providers"]
                selected = None
                skipped = []
                for provider_id in choices:
                    provider = catalog["providers"].get(provider_id)
                    if provider is None:
                        skipped.append(f"{provider_id}: provider not defined")
                        continue
                    for mode in provider["delivery"]:
                        if mode in client["consumes"]:
                            selected = (provider_id, mode)
                            break
                    if selected:
                        break
                    skipped.append(f"{provider_id}: no delivery consumed by {client_id}")
                control = client["control"] if selected else "none"
                if selected and control == "none":
                    # A provider was compatible, but the client offers no setup
                    # path at all. Say that rather than reporting success.
                    reason = f"{client_id} has no configurable setup path"
                elif skipped:
                    reason = "; ".join(skipped)
                else:
                    reason = "first compatible provider"
                row = {
                    "target": target["id"], "client": client_id,
                    "kind": kind, "item": item_id, "requested": choices.copy(),
                    "provider": selected[0] if selected else None,
                    "delivery": selected[1] if selected else None,
                    "control": control,
                    "reason": reason,
                }
                results.append(row)
    return results
