"""Regression tests for partial writes, shared config files and pre-write validation."""

import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import yaml

from fixtures import (FAILING_WRITER, TEXT_WRITER, commit, fake_writer,
                      make_catalogue, run_cli, write)

CODEX = ("id: codex-cli\nconsumes: [local-mcp, remote-mcp]\ncontrol: write\n"
         "observable: true\nconfig: {config}\nwriter: toml-region\n")
LOCAL_PROVIDER = "id: pa\ndelivery:\n  local-mcp:\n    command: /usr/bin/true\n"
UNMANAGED = 'model = "test"\n[mcp_servers.mine]\ncommand = "keep-me"\n'


def kit(targets, items="tools: [pa]"):
    lines = "\n".join(f"  - {{id: {t}, client: {c}}}" for t, c in targets)
    return f"name: k\ntargets:\n{lines}\n{items}\n"


class SharedConfigFileTests(unittest.TestCase):
    """Two targets pointing at one physical config file must work."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = make_catalogue(Path(self.temp.name))
        self.config = self.root / "config.toml"
        write(self.config, UNMANAGED)
        write(self.root / "clients/codex-cli.yaml", CODEX.format(config=self.config))
        write(self.root / "providers/pa.yaml", LOCAL_PROVIDER)
        self.kit = write(self.root / "kit.yaml",
                         kit([("laptop", "codex-cli"), ("desktop", "codex-cli")]))
        commit(self.root)
        self.base = ["--kit", str(self.kit), "--definitions", str(self.root)]

    def test_two_targets_share_one_config_file(self):
        code, out, err = run_cli(self.base + ["setup"])
        self.assertEqual(code, 0, err)
        text = self.config.read_text()
        self.assertIn("target=desktop,laptop", text)
        self.assertEqual(tomllib.loads(text)["mcp_servers"]["pa"], {"command": "/usr/bin/true"})
        # The unmanaged entry is untouched.
        self.assertEqual(tomllib.loads(text)["mcp_servers"]["mine"], {"command": "keep-me"})
        self.assertEqual(tomllib.loads(text)["model"], "test")
        lock = yaml.safe_load((self.root / "kit.lock.yaml").read_text())
        self.assertEqual(lock["pa"]["laptop"]["setup"], "complete")
        self.assertEqual(lock["pa"]["desktop"]["setup"], "complete")

    def test_repeated_setup_is_idempotent(self):
        self.assertEqual(run_cli(self.base + ["setup"])[0], 0)
        first = self.config.read_text()
        code, out, err = run_cli(self.base + ["setup"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.config.read_text(), first)
        self.assertIn("No configuration changes were required.", out)

    def test_diff_stays_read_only_for_shared_targets(self):
        before = self.config.read_text()
        code, out, _ = run_cli(self.base + ["diff"])
        self.assertEqual(code, 0)
        self.assertIn("Nothing written.", out)
        self.assertEqual(self.config.read_text(), before)
        self.assertFalse((self.root / "kit.lock.yaml").exists())


class RollbackTests(unittest.TestCase):
    """A failure partway through setup must leave nothing applied."""

    def test_earlier_write_is_rolled_back_when_a_later_write_fails(self):
        with tempfile.TemporaryDirectory() as temp, fake_writer("failing", FAILING_WRITER):
            root = make_catalogue(Path(temp))
            good = write(root / "good.toml", UNMANAGED)
            bad = root / "bad.conf"
            write(root / "clients/codex-cli.yaml", CODEX.format(config=good))
            write(root / "clients/broken.yaml",
                  f"id: broken\nconsumes: [local-mcp]\ncontrol: write\nobservable: true\n"
                  f"config: {bad}\nwriter: failing\n")
            write(root / "providers/pa.yaml", LOCAL_PROVIDER)
            kit_path = write(root / "kit.yaml",
                             kit([("laptop", "codex-cli"), ("other", "broken")]))
            commit(root)
            base = ["--kit", str(kit_path), "--definitions", str(root)]
            code, out, err = run_cli(base + ["setup"])
            self.assertEqual(code, 1)
            self.assertIn("always fails to commit", err)
            self.assertIn("no changes remain applied", err)
            # The first file is byte-identical to its pre-setup state.
            self.assertEqual(good.read_text(), UNMANAGED)
            self.assertNotIn("onekit", good.read_text())
            # No lock diverged from the untouched configuration.
            self.assertFalse((root / "kit.lock.yaml").exists())


class ValidateBeforeMutationTests(unittest.TestCase):
    """Invalid state must be found before any configuration is written."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = make_catalogue(Path(self.temp.name))
        self.config = self.root / "config.toml"
        write(self.config, UNMANAGED)
        write(self.root / "clients/codex-cli.yaml", CODEX.format(config=self.config))
        write(self.root / "providers/pa.yaml", LOCAL_PROVIDER)

    def _finish(self, kit_text):
        self.kit = write(self.root / "kit.yaml", kit_text)
        commit(self.root)
        return ["--kit", str(self.kit), "--definitions", str(self.root)]

    def assertUntouched(self):
        self.assertEqual(self.config.read_text(), UNMANAGED)
        self.assertFalse((self.root / "kit.lock.yaml").exists())

    def test_item_id_colliding_with_lock_metadata_is_rejected(self):
        write(self.root / "providers/definitions.yaml",
              "id: definitions\ndelivery:\n  local-mcp:\n    command: /usr/bin/true\n")
        base = self._finish(kit([("laptop", "codex-cli")], "tools: [definitions]"))
        code, out, err = run_cli(base + ["setup"])
        self.assertEqual(code, 1)
        self.assertIn("reserved by the lock file", err)
        self.assertUntouched()

    def test_malformed_existing_lock_is_rejected_before_writing(self):
        base = self._finish(kit([("laptop", "codex-cli")]))
        write(self.root / "kit.lock.yaml", "lock_version: 1\ndefinitions: x\n")
        code, out, err = run_cli(base + ["setup"])
        self.assertEqual(code, 1)
        self.assertIn("accepted_at", err)
        self.assertEqual(self.config.read_text(), UNMANAGED)

    def test_lock_with_a_bad_setup_state_is_rejected(self):
        base = self._finish(kit([("laptop", "codex-cli")]))
        write(self.root / "kit.lock.yaml",
              "lock_version: 1\ndefinitions: x\naccepted_at: now\n"
              "pa:\n  requested: [pa]\n  laptop:\n    provider: pa\n    delivery: local-mcp\n"
              "    control: write\n    setup: nonsense\n")
        code, out, err = run_cli(base + ["setup"])
        self.assertEqual(code, 1)
        self.assertIn("invalid setup state", err)
        self.assertEqual(self.config.read_text(), UNMANAGED)

    def test_unknown_writer_is_rejected_before_writing(self):
        write(self.root / "clients/codex-cli.yaml",
              CODEX.format(config=self.config).replace("toml-region", "no-such-writer"))
        base = self._finish(kit([("laptop", "codex-cli")]))
        code, out, err = run_cli(base + ["setup"])
        self.assertEqual(code, 1)
        self.assertIn("unknown writer: no-such-writer", err)
        self.assertUntouched()

    def test_two_clients_sharing_a_file_with_different_writers_are_rejected(self):
        with fake_writer("textw", TEXT_WRITER):
            write(self.root / "clients/other.yaml",
                  f"id: other\nconsumes: [remote-mcp]\ncontrol: write\nobservable: true\n"
                  f"config: {self.config}\nwriter: textw\n")
            write(self.root / "providers/pa.yaml",
                  "id: pa\ndelivery:\n  local-mcp:\n    command: /usr/bin/true\n"
                  "  remote-mcp:\n    url: https://example.invalid/mcp\n")
            base = self._finish(kit([("laptop", "codex-cli"), ("web", "other")]))
            code, out, err = run_cli(base + ["setup"])
            self.assertEqual(code, 1)
            self.assertIn("different writers", err)
            self.assertUntouched()


class DeliverySupportTests(unittest.TestCase):
    """A writer must never be handed a delivery mechanism it cannot express."""

    def test_exec_is_not_silently_written_as_mcp_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = make_catalogue(Path(temp))
            config = write(root / "config.toml", UNMANAGED)
            write(root / "clients/codex-cli.yaml",
                  f"id: codex-cli\nconsumes: [exec]\ncontrol: write\nobservable: true\n"
                  f"config: {config}\nwriter: toml-region\n")
            write(root / "providers/pa.yaml",
                  "id: pa\ndelivery:\n  exec:\n    command: /usr/bin/true\n")
            kit_path = write(root / "kit.yaml", kit([("laptop", "codex-cli")]))
            commit(root)
            base = ["--kit", str(kit_path), "--definitions", str(root)]
            code, out, err = run_cli(base + ["setup"])
            self.assertEqual(code, 1)
            self.assertIn("cannot express delivery 'exec'", err)
            self.assertEqual(config.read_text(), UNMANAGED)
            self.assertNotIn("mcp_servers.\"pa\"", config.read_text())
            self.assertFalse((root / "kit.lock.yaml").exists())


class WriterSeamTests(unittest.TestCase):
    """A contributed writer needs no dependency on the TOML writer."""

    def test_a_new_writer_works_end_to_end(self):
        with tempfile.TemporaryDirectory() as temp, fake_writer("textw", TEXT_WRITER):
            root = make_catalogue(Path(temp))
            config = root / "store.txt"
            write(root / "clients/textc.yaml",
                  f"id: textc\nconsumes: [remote-mcp]\ncontrol: write\nobservable: true\n"
                  f"config: {config}\nwriter: textw\n")
            write(root / "providers/pb.yaml",
                  "id: pb\ndelivery:\n  remote-mcp:\n    url: https://example.invalid/mcp\n")
            kit_path = write(root / "kit.yaml", kit([("t", "textc")], "tools: [pb]"))
            commit(root)
            base = ["--kit", str(kit_path), "--definitions", str(root)]
            code, out, err = run_cli(base + ["setup"])
            self.assertEqual(code, 0, err)
            self.assertEqual(config.read_text(), "pb=https://example.invalid/mcp\n")

    def test_a_new_writers_error_renders_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as temp, fake_writer("failing", FAILING_WRITER):
            root = make_catalogue(Path(temp))
            write(root / "clients/broken.yaml",
                  f"id: broken\nconsumes: [local-mcp]\ncontrol: write\nobservable: true\n"
                  f"config: {root / 'x.conf'}\nwriter: failing\n")
            write(root / "providers/pa.yaml", LOCAL_PROVIDER)
            kit_path = write(root / "kit.yaml", kit([("t", "broken")]))
            commit(root)
            code, out, err = run_cli(["--kit", str(kit_path), "--definitions", str(root), "setup"])
            self.assertEqual(code, 1)
            self.assertTrue(err.startswith("onekit: "), err)
            self.assertNotIn("Traceback", err)


class TruthfulOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = make_catalogue(Path(self.temp.name))
        self.config = write(self.root / "config.toml", UNMANAGED)
        write(self.root / "clients/codex-cli.yaml", CODEX.format(config=self.config))
        write(self.root / "providers/pa.yaml", LOCAL_PROVIDER)
        self.kit = write(self.root / "kit.yaml", kit([("laptop", "codex-cli")]))
        commit(self.root)
        self.base = ["--kit", str(self.kit), "--definitions", str(self.root)]

    def test_setup_never_claims_nothing_was_written(self):
        code, out, err = run_cli(self.base + ["setup"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("Nothing written.", out)
        self.assertIn("Applied 1 entry change(s)", out)
        self.assertIn("# >>> onekit", self.config.read_text())

    def test_diff_reports_nothing_written_and_writes_nothing(self):
        code, out, _ = run_cli(self.base + ["diff"])
        self.assertEqual(code, 0)
        self.assertIn("Nothing written.", out)
        self.assertEqual(self.config.read_text(), UNMANAGED)

    def test_force_shows_the_destructive_diff_before_asking_for_consent(self):
        self.assertEqual(run_cli(self.base + ["setup"])[0], 0)
        # Hand-edit the managed region so --force is genuinely destructive.
        self.config.write_text(self.config.read_text().replace("/usr/bin/true", "hand-edited"))
        seen = {}

        def answer(prompt):
            seen["stdout"] = capture["stdout"].getvalue()
            return "FORCE"

        import contextlib, io
        capture = {"stdout": io.StringIO()}
        with contextlib.redirect_stdout(capture["stdout"]), \
                mock.patch("builtins.input", answer), \
                mock.patch("sys.stdin.isatty", return_value=True):
            from core.cli import main
            code = main(self.base + ["setup", "--force"])
        self.assertEqual(code, 0)
        # The proposed change was on screen before consent was requested.
        self.assertIn("~ pa", seen["stdout"])
        self.assertNotIn("Applied", seen["stdout"])
        self.assertIn("/usr/bin/true", self.config.read_text())

    def test_force_is_refused_without_a_terminal_and_changes_nothing(self):
        self.assertEqual(run_cli(self.base + ["setup"])[0], 0)
        edited = self.config.read_text().replace("/usr/bin/true", "hand-edited")
        self.config.write_text(edited)
        code, out, err = run_cli(self.base + ["setup", "--force"])
        self.assertEqual(code, 1)
        self.assertIn("interactive human confirmation", err)
        self.assertEqual(self.config.read_text(), edited)


if __name__ == "__main__":
    unittest.main()
