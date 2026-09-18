"""Coverage for command paths the POC tests did not exercise."""

import tempfile
import unittest
from pathlib import Path

from fixtures import commit, make_catalogue, run_cli, write

WRITE_CLIENT = ("id: codex-cli\nconsumes: [local-mcp, remote-mcp]\ncontrol: write\n"
                "observable: true\nconfig: {config}\nwriter: toml-region\nprobe_command: sh\n")
MANUAL_CLIENT = ("id: chatgpt\nconsumes: [remote-mcp]\ncontrol: manual\nobservable: false\n"
                 "procedure: ['Add {url} for {item}']\n")


class ToolRequestTests(unittest.TestCase):
    """`tools:` names one provider directly, with no capability list."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = make_catalogue(Path(self.temp.name))
        self.config = write(self.root / "config.toml", 'model = "test"\n')
        write(self.root / "clients/codex-cli.yaml", WRITE_CLIENT.format(config=self.config))
        write(self.root / "clients/chatgpt.yaml", MANUAL_CLIENT)
        write(self.root / "providers/local-tool.yaml",
              "id: local-tool\ndelivery:\n  local-mcp:\n    command: /usr/bin/true\n")
        write(self.root / "providers/remote-tool.yaml",
              "id: remote-tool\ndelivery:\n  remote-mcp:\n    url: https://example.invalid/mcp\n")

    def _base(self, kit_text):
        kit_path = write(self.root / "kit.yaml", kit_text)
        commit(self.root)
        return ["--kit", str(kit_path), "--definitions", str(self.root)]

    def test_tools_resolve_without_a_capability_list(self):
        base = self._base("name: k\ntargets:\n  - {id: laptop, client: codex-cli}\n"
                          "tools: [local-tool, remote-tool]\n")
        code, out, err = run_cli(base + ["resolve"])
        self.assertEqual(code, 0, err)
        self.assertIn("laptop / local-tool: local-tool (local-mcp, write)", out)
        self.assertIn("laptop / remote-tool: remote-tool (remote-mcp, write)", out)

    def test_a_tool_the_client_cannot_consume_is_blocked_not_substituted(self):
        base = self._base("name: k\ntargets:\n  - {id: web, client: chatgpt}\n"
                          "tools: [local-tool]\n")
        code, out, err = run_cli(base + ["resolve"])
        self.assertEqual(code, 0, err)
        self.assertIn("web / local-tool: blocked", out)
        self.assertIn("no delivery consumed by chatgpt", out)

    def test_a_tool_missing_from_the_catalogue_is_blocked_with_a_reason(self):
        base = self._base("name: k\ntargets:\n  - {id: laptop, client: codex-cli}\n"
                          "tools: [not-in-catalogue]\n")
        code, out, err = run_cli(base + ["resolve"])
        self.assertEqual(code, 0, err)
        self.assertIn("not-in-catalogue: provider not defined", out)

    def test_tools_are_written_and_locked_like_capabilities(self):
        base = self._base("name: k\ntargets:\n  - {id: laptop, client: codex-cli}\n"
                          "tools: [local-tool]\n")
        code, out, err = run_cli(base + ["setup"])
        self.assertEqual(code, 0, err)
        self.assertIn('[mcp_servers."local-tool"]', self.config.read_text())
        import yaml
        lock = yaml.safe_load((self.root / "kit.lock.yaml").read_text())
        self.assertEqual(lock["local-tool"]["requested"], ["local-tool"])
        self.assertEqual(lock["local-tool"]["laptop"]["setup"], "complete")


class ProbeTests(unittest.TestCase):
    def test_probe_reports_each_client_and_needs_no_kit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = make_catalogue(Path(temp))
            write(root / "clients/codex-cli.yaml", WRITE_CLIENT.format(config="/tmp/x.toml"))
            write(root / "clients/chatgpt.yaml", MANUAL_CLIENT)
            # No --kit is supplied and no kit.yaml exists here.
            code, out, err = run_cli(["--definitions", str(root), "probe"])
            self.assertEqual(code, 0, err)
            self.assertIn("codex-cli: installed (", out)
            self.assertIn("control=write", out)
            self.assertIn("chatgpt: account access not programmatically verified", out)
            self.assertIn("control=manual; observable=false; consumes=remote-mcp", out)

    def test_probe_reports_a_client_that_is_not_installed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = make_catalogue(Path(temp))
            write(root / "clients/ghost.yaml",
                  "id: ghost\nconsumes: [local-mcp]\ncontrol: manual\nobservable: false\n"
                  "procedure: ['x']\nprobe_command: onekit-no-such-binary\n")
            code, out, err = run_cli(["--definitions", str(root), "probe"])
            self.assertEqual(code, 0, err)
            self.assertIn("ghost: not found in PATH", out)

    def test_probe_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = make_catalogue(Path(temp))
            write(root / "clients/chatgpt.yaml", MANUAL_CLIENT)
            before = sorted(p.name for p in root.rglob("*"))
            self.assertEqual(run_cli(["--definitions", str(root), "probe"])[0], 0)
            self.assertEqual(sorted(p.name for p in root.rglob("*")), before)


class ControlNoneTests(unittest.TestCase):
    """A client with no setup path blocks items and says why."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = make_catalogue(Path(self.temp.name))
        write(self.root / "clients/locked-down.yaml",
              "id: locked-down\nconsumes: [remote-mcp]\ncontrol: none\nobservable: false\n")
        write(self.root / "providers/pb.yaml",
              "id: pb\ndelivery:\n  remote-mcp:\n    url: https://example.invalid/mcp\n")
        self.kit = write(self.root / "kit.yaml",
                         "name: k\ntargets:\n  - {id: t, client: locked-down}\ntools: [pb]\n")
        commit(self.root)
        self.base = ["--kit", str(self.kit), "--definitions", str(self.root)]

    def test_reason_names_the_missing_setup_path_not_a_compatible_provider(self):
        code, out, err = run_cli(self.base + ["resolve"])
        self.assertEqual(code, 0, err)
        self.assertIn("locked-down has no configurable setup path", out)
        self.assertNotIn("first compatible provider", out)

    def test_blocked_item_is_reported_by_diff_and_doctor(self):
        code, out, _ = run_cli(self.base + ["diff"])
        self.assertEqual(code, 0)
        self.assertIn("t / pb: blocked;", out)
        code, out, _ = run_cli(self.base + ["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("pb / t: blocked", out)
        self.assertIn("t: 0/1 available, 0 configured", out)

    def test_setup_writes_nothing_for_a_control_none_target(self):
        code, out, err = run_cli(self.base + ["setup"])
        self.assertEqual(code, 0, err)
        self.assertIn("No configuration changes were required.", out)
        import yaml
        lock = yaml.safe_load((self.root / "kit.lock.yaml").read_text())
        self.assertEqual(lock["pb"]["t"]["control"], "none")
        self.assertEqual(lock["pb"]["t"]["setup"], "pending")


if __name__ == "__main__":
    unittest.main()
