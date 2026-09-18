"""Managed configuration writers.

A writer owns one client configuration format. OneKit Core never imports a
concrete writer: it loads one by name from a client definition and talks to it
through the contract below.

A writer module must provide:

``SUPPORTED_DELIVERIES``
    A container of the delivery mechanism ids the writer can express. The
    compiler refuses an unsupported combination before anything is mutated.

``plan(path, kit, targets, entries, force=False) -> dict``
    Read the current configuration and return a plan. ``entries`` maps a
    provider id to ``{"delivery": str, "payload": dict}``. The returned plan
    must include ``path``, ``changes`` (a list of ``(symbol, name)`` pairs) and
    whatever private state ``apply`` and ``revert`` need. It must not mutate
    anything.

``apply(plan) -> None``
    Commit the plan, refusing if the file changed since ``plan`` ran.

``revert(plan) -> None``
    Restore the state that existed before ``apply``, so a failed multi-file
    setup can roll back. It must refuse rather than clobber if the target has
    changed since ``apply`` committed.

Every failure a writer reports must be a :class:`WriterError` so the CLI can
render it without knowing which writer produced it.
"""

from __future__ import annotations


class WriterError(ValueError):
    """A writer refused to change a configuration, or could not do so safely.

    This is the writer-neutral error contract. New writers raise this (or a
    subclass of it) and need no dependency on any other writer.
    """
