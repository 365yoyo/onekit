"""OneKit must run as an installed package, not only from a Git checkout."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from fixtures import REPO, make_catalogue, write

CATALOGUE_KINDS = ("clients", "providers", "capabilities")


def run(argv, cwd, env=None):
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)


class PackagedLayoutTests(unittest.TestCase):
    """The catalogue is found inside the package, independently of the cwd."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.site = Path(self.temp.name) / "site"
        self.site.mkdir()
        # Reproduce the installed layout: no top-level catalogue, no Git.
        for package in ("core", "writers"):
            shutil.copytree(REPO / package, self.site / package,
                            ignore=shutil.ignore_patterns("__pycache__"))
        for kind in CATALOGUE_KINDS:
            shutil.copytree(REPO / kind, self.site / "core" / kind)
        self.elsewhere = Path(self.temp.name) / "elsewhere"
        self.elsewhere.mkdir()
        self.env = {**os.environ, "PYTHONPATH": str(self.site),
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def test_layout_has_no_checkout_catalogue(self):
        for kind in CATALOGUE_KINDS:
            self.assertFalse((self.site / kind).exists())
            self.assertTrue((self.site / "core" / kind).is_dir())
        self.assertFalse((self.site / ".git").exists())

    def test_probe_works_from_an_unrelated_directory(self):
        result = run([sys.executable, "-m", "core.cli", "probe"], self.elsewhere, self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("codex-cli:", result.stdout)
        self.assertIn("chatgpt:", result.stdout)

    def test_resolve_uses_the_packaged_catalogue(self):
        kit = write(self.elsewhere / "kit.yaml",
                    "name: k\ntargets:\n  - {id: laptop, client: codex-cli}\n"
                    "  - {id: web, client: chatgpt}\ncapabilities: [web-search]\n")
        result = run([sys.executable, "-m", "core.cli", "--kit", str(kit), "resolve"],
                     self.elsewhere, self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("laptop / web-search: search-a (local-mcp, write)", result.stdout)
        self.assertIn("web / web-search: search-b (remote-mcp, manual)", result.stdout)


@unittest.skipIf(os.environ.get("ONEKIT_SKIP_INSTALL_TEST"),
                 "ONEKIT_SKIP_INSTALL_TEST is set")
class NonEditableInstallTests(unittest.TestCase):
    """A real `pip install .` must produce a working onekit."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        base = Path(cls.temp.name)
        venv = base / "venv"
        created = run([sys.executable, "-m", "venv", str(venv)], base)
        if created.returncode != 0:
            cls.temp.cleanup()
            raise unittest.SkipTest(f"cannot create a venv: {created.stderr[-300:]}")
        cls.python = venv / "bin" / "python"
        cls.onekit = venv / "bin" / "onekit"
        # Build from a copy so pip's build artefacts never land in the checkout.
        source = base / "source"
        shutil.copytree(REPO, source, ignore=shutil.ignore_patterns(
            ".git", "build", "dist", "*.egg-info", ".venv", "__pycache__"))
        installed = run([str(cls.python), "-m", "pip", "install", "--quiet", str(source)], base)
        if installed.returncode != 0:
            cls.temp.cleanup()
            raise unittest.SkipTest(
                f"cannot install OneKit (no package index?): {installed.stderr[-300:]}")
        cls.base = base
        # Never let an inherited PYTHONPATH put the checkout ahead of the install.
        cls.env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temp"):
            cls.temp.cleanup()

    def test_installed_tree_has_no_git_and_ships_the_catalogue(self):
        located = run([str(self.python), "-c",
                       "import core, pathlib; print(pathlib.Path(core.__file__).parent)"],
                      self.base, self.env)
        package = Path(located.stdout.strip())
        self.assertIn("site-packages", str(package))
        for kind in CATALOGUE_KINDS:
            self.assertTrue((package / kind).is_dir(), f"{kind} not packaged")
            self.assertTrue(list((package / kind).glob("*.yaml")))
        self.assertFalse((package.parent / ".git").exists())

    def test_probe_runs_from_the_installed_console_script(self):
        result = run([str(self.onekit), "probe"], self.base, self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("codex-cli:", result.stdout)

    def test_setup_records_version_provenance_without_git(self):
        root = make_catalogue(self.base / "defs")
        config = self.base / "client.toml"
        write(config, 'model = "test"\n')
        write(root / "clients/codex-cli.yaml",
              f"id: codex-cli\nconsumes: [local-mcp]\ncontrol: write\nobservable: true\n"
              f"config: {config}\nwriter: toml-region\n")
        write(root / "providers/pa.yaml",
              "id: pa\ndelivery:\n  local-mcp:\n    command: /usr/bin/true\n")
        kit = write(self.base / "kit.yaml",
                    "name: k\ntargets:\n  - {id: laptop, client: codex-cli}\ntools: [pa]\n")
        self.assertFalse((root / ".git").exists())
        result = run([str(self.onekit), "--kit", str(kit), "--definitions", str(root), "setup"],
                     self.base, self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('[mcp_servers."pa"]', config.read_text())
        self.assertIn('model = "test"', config.read_text())
        lock = yaml.safe_load((self.base / "kit.lock.yaml").read_text())
        # No repository, so the release version identifies the definitions.
        self.assertTrue(lock["definitions"].startswith("onekit-defs@v"), lock["definitions"])
        self.assertEqual(lock["pa"]["laptop"]["setup"], "complete")


if __name__ == "__main__":
    unittest.main()
