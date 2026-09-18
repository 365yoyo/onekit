"""Shared helpers for OneKit tests."""

import contextlib
import importlib
import io
import subprocess
import sys
import tempfile
from pathlib import Path

from core.cli import main

REPO = Path(__file__).resolve().parent.parent


def make_catalogue(root: Path) -> Path:
    for name in ("clients", "providers", "capabilities"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def commit(root: Path) -> None:
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), *identity, "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), *identity, "commit", "-qm", "fixture"], check=True)


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def fake_writer(name: str, source: str):
    """Expose a writer module to `writers.<name>` from a real file on disk.

    This exercises the same import path a contributed writer would take,
    without adding a module to the shipped package.
    """
    package = importlib.import_module("writers")
    with tempfile.TemporaryDirectory() as temp:
        Path(temp, f"{name}.py").write_text(source)
        package.__path__.append(temp)
        importlib.invalidate_caches()
        try:
            yield
        finally:
            package.__path__.remove(temp)
            sys.modules.pop(f"writers.{name}", None)
            importlib.invalidate_caches()


#: A writer that plans normally but always fails when committing.
FAILING_WRITER = '''
from pathlib import Path
from writers import WriterError

SUPPORTED_DELIVERIES = frozenset({"local-mcp", "remote-mcp"})


def plan(path, kit, targets, entries, force=False):
    return {"path": Path(path), "existed": Path(path).exists(), "original": "",
            "proposed": "written", "changes": [("+", name) for name in sorted(entries)],
            "targets": sorted(targets)}


def apply(plan_data):
    raise WriterError("this writer always fails to commit")


def revert(plan_data):
    return None
'''

#: A minimal but complete writer, used to prove the extension seam.
TEXT_WRITER = '''
from pathlib import Path
from writers import WriterError

SUPPORTED_DELIVERIES = frozenset({"remote-mcp"})


def plan(path, kit, targets, entries, force=False):
    path = Path(path)
    existed = path.exists()
    original = path.read_text() if existed else ""
    body = "".join(f"{name}={entry['payload']['url']}\\n" for name, entry in sorted(entries.items()))
    return {"path": path, "existed": existed, "original": original, "proposed": body,
            "changes": [("+", name) for name in sorted(entries)], "targets": sorted(targets)}


def apply(plan_data):
    if plan_data["path"].exists() and plan_data["path"].read_text() != plan_data["original"]:
        raise WriterError("changed after diff")
    plan_data["path"].write_text(plan_data["proposed"])


def revert(plan_data):
    if plan_data["existed"]:
        plan_data["path"].write_text(plan_data["original"])
    else:
        plan_data["path"].unlink(missing_ok=True)
'''
