import tempfile
import tomllib
import unittest
from pathlib import Path

from writers.toml_region import RegionError, apply, plan


class TomlRegionTests(unittest.TestCase):
    def test_add_remove_preserves_unmanaged_and_rejects_hand_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            unmanaged = 'model = "test"\n[mcp_servers.other]\ncommand = "true"\n'
            path.write_text(unmanaged)
            entry = {"search-a": {"command": "npx", "args": ["-y", "server"]}}
            first = plan(path, "my-kit", "laptop", entry)
            self.assertEqual(first["changes"], [("+", "search-a")])
            apply(first)
            self.assertIn(unmanaged, path.read_text())
            self.assertEqual(tomllib.loads(path.read_text())["mcp_servers"]["search-a"], entry["search-a"])
            path.write_text(path.read_text().replace('command = "npx"', 'command = "edited"'))
            with self.assertRaisesRegex(RegionError, "integrity"):
                plan(path, "my-kit", "laptop", {})
            path.write_text(first["proposed"])
            removal = plan(path, "my-kit", "laptop", {})
            self.assertEqual(removal["changes"], [("-", "search-a")])
            apply(removal)
            self.assertEqual(tomllib.loads(path.read_text())["mcp_servers"], {"other": {"command": "true"}})

    def test_unmanaged_collision_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text('[mcp_servers."search-a"]\ncommand = "mine"\n')
            with self.assertRaisesRegex(RegionError, "overlap"):
                plan(path, "my-kit", "laptop", {"search-a": {"command": "other"}})

    def test_stale_parallel_plan_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            first = plan(path, "my-kit", "laptop", {"a": {"command": "one"}})
            second = plan(path, "my-kit", "laptop", {"b": {"command": "two"}})
            apply(first)
            with self.assertRaisesRegex(RegionError, "changed after diff"):
                apply(second)
