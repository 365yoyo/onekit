import tempfile
import unittest
from pathlib import Path

from core.loader import DefinitionError, load_catalog, load_kit
from core.resolver import resolve


class ResolutionTests(unittest.TestCase):
    def test_ordered_capability_substitutes_for_manual_remote_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("clients", "providers", "capabilities"):
                (root / name).mkdir()
            (root / "clients/codex-cli.yaml").write_text(
                "id: codex-cli\nconsumes: [local-mcp, remote-mcp]\n"
                "control: write\nobservable: true\nconfig: /tmp/config.toml\nwriter: toml-region\n"
            )
            (root / "clients/chatgpt.yaml").write_text(
                "id: chatgpt\nconsumes: [remote-mcp]\ncontrol: manual\n"
                "observable: false\nprocedure: ['Add {url} in ChatGPT']\n"
            )
            (root / "providers/search-a.yaml").write_text(
                "id: search-a\ndelivery:\n  local-mcp:\n    command: search-a\n"
            )
            (root / "providers/search-b.yaml").write_text(
                "id: search-b\ndelivery:\n  remote-mcp:\n"
                "    url: https://example.invalid/mcp\n"
            )
            (root / "capabilities/web-search.yaml").write_text(
                "id: web-search\nproviders: [search-a, search-b]\n"
            )
            kit_path = root / "kit.yaml"
            kit_path.write_text(
                "name: my-kit\ntargets:\n"
                "  - {id: laptop, client: codex-cli}\n"
                "  - {id: work-chat, client: chatgpt}\n"
                "capabilities: [web-search]\n"
            )
            original = kit_path.read_bytes()
            rows = resolve(load_kit(kit_path), load_catalog(root))
            self.assertEqual(kit_path.read_bytes(), original)
            self.assertEqual([r["provider"] for r in rows], ["search-a", "search-b"])
            self.assertEqual([r["control"] for r in rows], ["write", "manual"])
            self.assertIn("search-a: no delivery consumed", rows[1]["reason"])
            self.assertEqual(rows[1]["requested"], ["search-a", "search-b"])

    def test_kit_rejects_client_specific_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "kit.yaml"
            path.write_text(
                "name: my-kit\ntargets: [{id: laptop, client: codex-cli}]\n"
                "config: ~/.codex/config.toml\n"
            )
            with self.assertRaisesRegex(DefinitionError, "unsupported fields"):
                load_kit(path)

    def test_new_provider_and_capability_need_only_yaml(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("clients", "providers", "capabilities"):
                (root / name).mkdir()
            (root / "clients/new-client.yaml").write_text(
                "id: new-client\nconsumes: [remote-mcp]\ncontrol: manual\n"
                "observable: false\nprocedure: ['Enter {url}']\n")
            (root / "providers/another-search.yaml").write_text(
                "id: another-search\ndelivery:\n  remote-mcp:\n"
                "    url: https://example.org/mcp\n")
            (root / "capabilities/another-web-search.yaml").write_text(
                "id: another-web-search\nproviders: [another-search]\n")
            kit_path = root / "kit.yaml"
            kit_path.write_text("name: test-kit\ntargets: [{id: web, client: new-client}]\n"
                                "capabilities: [another-web-search]\n")
            rows = resolve(load_kit(kit_path), load_catalog(root))
            self.assertEqual((rows[0]["provider"], rows[0]["delivery"], rows[0]["control"]),
                             ("another-search", "remote-mcp", "manual"))


if __name__ == "__main__":
    unittest.main()
