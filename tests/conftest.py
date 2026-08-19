import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingestion"))
sys.path.insert(0, str(ROOT / "mcp_server"))

import ingest as ingest_module  # noqa: E402
import mcp_server as mcp_server_module  # noqa: E402

BRAIN_CONFIG = {
    "display_name": "Ada Lovelace",
    "expertise_tags": ["math", "computing"],
    "style_notes": "  Precise and analytical.  ",
}


@pytest.fixture
def ingest(tmp_path, monkeypatch):
    """ingest module with filesystem paths and API keys redirected to a sandbox."""
    brains_dir = tmp_path / "brains"
    brains_dir.mkdir()
    monkeypatch.setattr(ingest_module, "ROOT", tmp_path)
    monkeypatch.setattr(ingest_module, "BRAINS_DIR", brains_dir)
    monkeypatch.setattr(ingest_module, "STATE_FILE", tmp_path / "ingestion" / "state.json")
    monkeypatch.setattr(ingest_module, "CONFIG_FILE", tmp_path / "brains.yaml")
    monkeypatch.setattr(ingest_module, "YT_API_KEY", "yt-key")
    monkeypatch.setattr(ingest_module, "OPENROUTER_KEY", "")
    monkeypatch.setattr(ingest_module, "GITHUB_TOKEN", "")
    monkeypatch.setattr(ingest_module, "GITHUB_REPO", "")
    monkeypatch.setattr(ingest_module.time, "sleep", lambda *_: None)
    return ingest_module


@pytest.fixture
def brains_dir(ingest):
    return ingest.BRAINS_DIR


@pytest.fixture
def server(tmp_path, monkeypatch):
    """mcp_server module reading config/brains from a sandbox directory."""
    brains_dir = tmp_path / "brains"
    brains_dir.mkdir()
    config_file = tmp_path / "brains.yaml"
    config_file.write_text(
        "brains:\n"
        "  ada:\n"
        '    display_name: "Ada Lovelace"\n'
        "    expertise_tags: [math, computing]\n"
        '    style_notes: "Precise and analytical."\n'
        "  grace:\n"
        '    display_name: "Grace Hopper"\n'
        "    expertise_tags: [compilers]\n"
        '    style_notes: "Pragmatic."\n'
    )
    monkeypatch.setattr(mcp_server_module, "CONFIG_FILE", config_file)
    monkeypatch.setattr(mcp_server_module, "BRAINS_DIR", brains_dir)
    monkeypatch.setattr(mcp_server_module, "OPENROUTER_KEY", "or-key")
    monkeypatch.setattr(mcp_server_module, "MCP_AUTH_TOKEN", "")
    return mcp_server_module


class DummyResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, json_data=None, text="", status_code=200):
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
