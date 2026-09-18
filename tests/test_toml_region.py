import tempfile
import tomllib
import unittest
from pathlib import Path

from writers import WriterError
from writers.toml_region import RegionError, apply, plan, revert


def stdio(command, args=None):
    payload = {"command": command}
    if args is not None:
        payload["args"] = args
    return {"delivery": "local-mcp", "payload": payload}


class TomlRegionTests(unittest.TestCase):
    def test_add_remove_preserves_unmanaged_and_rejects_hand_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            unmanaged = 'model = "test"\n[mcp_servers.other]\ncommand = "true"\n'
            path.write_text(unmanaged)
            entry = {"search-a": stdio("npx", ["-y", "server"])}
            first = plan(path, "my-kit", ["laptop"], entry)
            self.assertEqual(first["changes"], [("+", "search-a")])
            apply(first)
            self.assertIn(unmanaged, path.read_text())
            self.assertEqual(tomllib.loads(path.read_text())["mcp_servers"]["search-a"],
                             entry["search-a"]["payload"])
            path.write_text(path.read_text().replace('command = "npx"', 'command = "edited"'))
            with self.assertRaisesRegex(RegionError, "integrity"):
                plan(path, "my-kit", ["laptop"], {})
            path.write_text(first["proposed"])
            removal = plan(path, "my-kit", ["laptop"], {})
            self.assertEqual(removal["changes"], [("-", "search-a")])
            apply(removal)
            self.assertEqual(tomllib.loads(path.read_text())["mcp_servers"], {"other": {"command": "true"}})

    def test_unmanaged_collision_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text('[mcp_servers."search-a"]\ncommand = "mine"\n')
            with self.assertRaisesRegex(RegionError, "overlap"):
                plan(path, "my-kit", ["laptop"], {"search-a": stdio("other")})

    def test_stale_parallel_plan_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            first = plan(path, "my-kit", ["laptop"], {"a": stdio("one")})
            second = plan(path, "my-kit", ["laptop"], {"b": stdio("two")})
            apply(first)
            with self.assertRaisesRegex(RegionError, "changed after diff"):
                apply(second)

    def test_region_error_is_a_writer_error(self):
        self.assertTrue(issubclass(RegionError, WriterError))

    def test_unsupported_delivery_refused_by_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            entries = {"tool": {"delivery": "exec", "payload": {"command": "/usr/bin/true"}}}
            with self.assertRaisesRegex(RegionError, "cannot express delivery 'exec'"):
                plan(path, "my-kit", ["laptop"], entries)
            self.assertFalse(path.exists())

    def test_revert_restores_previous_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            original = 'model = "test"\n'
            path.write_text(original)
            change = plan(path, "my-kit", ["laptop"], {"a": stdio("one")})
            apply(change)
            self.assertIn("# >>> onekit", path.read_text())
            revert(change)
            self.assertEqual(path.read_text(), original)

    def test_revert_removes_a_file_it_created(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            change = plan(path, "my-kit", ["laptop"], {"a": stdio("one")})
            apply(change)
            self.assertTrue(path.exists())
            revert(change)
            self.assertFalse(path.exists())

    def test_revert_refuses_to_clobber_a_later_change(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text('model = "test"\n')
            change = plan(path, "my-kit", ["laptop"], {"a": stdio("one")})
            apply(change)
            path.write_text('model = "someone else edited this"\n')
            with self.assertRaisesRegex(RegionError, "not reverting"):
                revert(change)
            self.assertEqual(path.read_text(), 'model = "someone else edited this"\n')

    def test_two_targets_share_one_region(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text('model = "test"\n')
            change = plan(path, "my-kit", ["laptop", "desktop"], {"a": stdio("one")})
            apply(change)
            self.assertIn("target=desktop,laptop", path.read_text())
            self.assertEqual(tomllib.loads(path.read_text())["mcp_servers"]["a"],
                             {"command": "one"})

    def test_region_owned_by_another_kit_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            apply(plan(path, "kit-one", ["laptop"], {"a": stdio("one")}))
            with self.assertRaisesRegex(RegionError, "belongs to Kit 'kit-one'"):
                plan(path, "kit-two", ["laptop"], {"a": stdio("one")})


if __name__ == "__main__":
    unittest.main()
