import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

import yaml

from core.cli import main
from core.doctor import report
from core.loader import load_catalog, load_kit
from core.resolver import resolve


class PocTests(unittest.TestCase):
    def test_p1_p2_p4_in_isolated_catalogue_and_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("clients", "providers", "capabilities"):
                (root / name).mkdir()
            config = root / "config.toml"
            config.write_text('model = "test"\n')
            (root / "clients/codex-cli.yaml").write_text(
                f"id: codex-cli\nconsumes: [local-mcp, remote-mcp]\ncontrol: write\n"
                f"observable: true\nconfig: {config}\nwriter: toml-region\n")
            (root / "clients/chatgpt.yaml").write_text(
                "id: chatgpt\nconsumes: [remote-mcp]\ncontrol: manual\nobservable: false\n"
                "procedure: ['Create {name} at {url} in ChatGPT']\n")
            (root / "providers/search-a.yaml").write_text(
                "id: search-a\ndelivery:\n  local-mcp:\n    command: /usr/bin/true\n")
            (root / "providers/search-b.yaml").write_text(
                "id: search-b\ndelivery:\n  remote-mcp:\n    url: https://example.invalid/mcp\n")
            (root / "capabilities/web-search.yaml").write_text(
                "id: web-search\nproviders: [search-a, search-b]\n")
            kit_path = root / "kit.yaml"
            kit_path.write_text(
                "name: test-kit\ntargets:\n"
                "  - {id: laptop, client: codex-cli}\n"
                "  - {id: work-chat, client: chatgpt}\n"
                "capabilities: [web-search]\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test",
                            "-c", "user.email=test@example.invalid", "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test",
                            "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
            base = ["--kit", str(kit_path), "--definitions", str(root)]
            original_kit = kit_path.read_bytes()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(base + ["setup"]), 0)
            self.assertIn("Create search-b at https://example.invalid/mcp", output.getvalue())
            self.assertEqual(kit_path.read_bytes(), original_kit)
            self.assertIn("# >>> onekit", config.read_text())
            if shutil.which("codex"):
                isolated_env = {**os.environ, "CODEX_HOME": str(root)}
                listed = subprocess.run(["codex", "mcp", "list", "--json"],
                                        env=isolated_env, capture_output=True,
                                        text=True, check=True)
                self.assertIn("search-a", [entry["name"] for entry in json.loads(listed.stdout)])
            lock_path = root / "kit.lock.yaml"
            lock = yaml.safe_load(lock_path.read_text())
            self.assertIn("onekit-defs@", lock["definitions"])
            self.assertEqual(lock["web-search"]["requested"], ["search-a", "search-b"])
            self.assertEqual(lock["web-search"]["laptop"]["setup"], "complete")
            self.assertEqual(lock["web-search"]["work-chat"]["setup"], "pending")
            before_doctor = {path.relative_to(root): path.read_bytes()
                             for path in root.rglob("*") if path.is_file() and ".git" not in path.parts}
            doctor_output = io.StringIO()
            with contextlib.redirect_stdout(doctor_output):
                self.assertEqual(main(base + ["doctor", "--fixture", "search-a-down"]), 0)
            self.assertIn("Web Search / laptop", doctor_output.getvalue())
            self.assertIn("locked:       search-a", doctor_output.getvalue())
            self.assertIn("observed:     failing (fixture)", doctor_output.getvalue())
            self.assertIn("next in list: search-b", doctor_output.getvalue())
            self.assertIn("tool names and schemas differ", doctor_output.getvalue())
            self.assertIn("No change made.", doctor_output.getvalue())
            after_doctor = {path.relative_to(root): path.read_bytes()
                            for path in root.rglob("*") if path.is_file() and ".git" not in path.parts}
            self.assertEqual(after_doctor, before_doctor)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(base + ["confirm", "work-chat", "web-search"]), 0)
            lock = yaml.safe_load(lock_path.read_text())
            self.assertEqual(lock["web-search"]["work-chat"]["setup"], "asserted")
            confirmed_doctor = io.StringIO()
            with contextlib.redirect_stdout(confirmed_doctor):
                self.assertEqual(main(base + ["doctor"]), 0)
            self.assertIn("web-search / work-chat: configured (asserted)", confirmed_doctor.getvalue())
            catalogue = load_catalog(root)
            rows = resolve(load_kit(kit_path), catalogue)
            fixture = report(rows, lock, catalogue, "search-a-down")
            self.assertEqual(fixture["drift"][0]["next"], "search-b")
            self.assertEqual(fixture["counts"]["work-chat"], {"available": 1, "configured": 1})
            config.write_text(config.read_text().replace('command = "/usr/bin/true"', 'command = "edited"'))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(base + ["setup"]), 1)
                self.assertEqual(main(base + ["setup", "--force"]), 1)
            self.assertIn("integrity mismatch", stderr.getvalue())
            self.assertIn("interactive human confirmation", stderr.getvalue())
            self.assertIn('command = "edited"', config.read_text())
            config.write_text(config.read_text().replace('command = "edited"', 'command = "/usr/bin/true"'))
            kit_path.write_text(kit_path.read_text().replace("capabilities: [web-search]", "capabilities: []"))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(base + ["setup"]), 0)
            self.assertEqual(tomllib.loads(config.read_text()), {"model": "test"})


if __name__ == "__main__":
    unittest.main()
