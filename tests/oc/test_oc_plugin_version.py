import json
import re

from support import REPO


def test_plugin_version_matches_coven():
    source = (REPO / "opencode" / "src" / "version.ts").read_text()
    found = re.search(r'export const VERSION = "([^"]+)";', source)
    assert found is not None
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert found.group(1) == manifest["version"]
