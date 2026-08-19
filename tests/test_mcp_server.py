import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from conftest import DummyResponse


@pytest.fixture
def client(server):
    return TestClient(server.app)


def _write_brain(server, slug, body="content", entries=1):
    text = f"# {slug} — Knowledge Brain\n\n---\n\n"
    for i in range(entries):
        text += f"## Entry {i}\n\n{body}\n\n---\n\n"
    (server.BRAINS_DIR / f"{slug}.md").write_text(text)


class FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_verify_auth_allows_everything_when_no_token_configured(server):
    assert server.verify_auth(FakeRequest()) is None


def test_verify_auth_accepts_matching_bearer_token(server, monkeypatch):
    monkeypatch.setattr(server, "MCP_AUTH_TOKEN", "secret")
    assert server.verify_auth(FakeRequest({"Authorization": "Bearer secret"})) is None


@pytest.mark.parametrize(
    "header",
    ["", "Bearer wrong", "Basic secret", "secret"],
)
def test_verify_auth_rejects_bad_credentials(server, monkeypatch, header):
    monkeypatch.setattr(server, "MCP_AUTH_TOKEN", "secret")
    with pytest.raises(HTTPException) as excinfo:
        server.verify_auth(FakeRequest({"Authorization": header} if header else {}))
    assert excinfo.value.status_code == 401


# ── Brain loading ─────────────────────────────────────────────────────────────

def test_load_config_returns_brains_mapping(server):
    config = server.load_config()
    assert set(config) == {"ada", "grace"}
    assert config["ada"]["display_name"] == "Ada Lovelace"


def test_load_brain_content_missing_file_returns_empty_string(server):
    assert server.load_brain_content("ada") == ""


def test_load_brain_content_returns_full_file_when_small(server):
    _write_brain(server, "ada", body="short body")
    assert "short body" in server.load_brain_content("ada")
    assert "trimmed" not in server.load_brain_content("ada")


def test_load_brain_content_trims_large_file_but_keeps_header_and_tail(server, monkeypatch):
    monkeypatch.setattr(server, "MAX_BRAIN_CHARS", 500)
    header = "# Ada Lovelace — Knowledge Brain\n\n---\n\n"
    (server.BRAINS_DIR / "ada.md").write_text(header + "x" * 2000 + "NEWEST_ENTRY")

    content = server.load_brain_content("ada")

    assert content.startswith(header)
    assert "[...earlier entries trimmed for context...]" in content
    assert content.endswith("NEWEST_ENTRY")
    assert len(content) < 2000


def test_available_brains_reports_entry_counts(server):
    _write_brain(server, "ada", entries=3)
    brains = {b["slug"]: b for b in server.available_brains()}

    assert brains["ada"]["has_content"] is True
    assert brains["ada"]["entry_count"] == 3
    assert brains["ada"]["tags"] == ["math", "computing"]
    assert brains["grace"]["has_content"] is False
    assert brains["grace"]["entry_count"] == 0


# ── LLM query ─────────────────────────────────────────────────────────────────

def test_query_llm_without_key_returns_error_message(server, monkeypatch):
    monkeypatch.setattr(server, "OPENROUTER_KEY", "")
    assert "OPENROUTER_API_KEY not configured" in server.query_llm("sys", "hi")


def test_query_llm_sends_system_and_user_messages(server, monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["headers"] = headers
        captured["json"] = json
        return DummyResponse({"choices": [{"message": {"content": "the answer"}}]})

    monkeypatch.setattr(server.requests, "post", fake_post)
    assert server.query_llm("sys prompt", "user question") == "the answer"
    assert captured["headers"]["Authorization"] == "Bearer or-key"
    assert captured["json"]["messages"] == [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "user question"},
    ]


def test_query_llm_returns_error_string_on_failure(server, monkeypatch):
    monkeypatch.setattr(server.requests, "post", lambda *a, **k: DummyResponse(status_code=502))
    assert server.query_llm("sys", "hi").startswith("Error querying LLM:")


def test_build_system_prompt_includes_style_and_content(server):
    prompt = server.build_system_prompt(
        "ada",
        {"display_name": "Ada Lovelace", "style_notes": "  Precise.  "},
        "KB BODY",
    )
    assert "Ada Lovelace" in prompt
    assert "Precise." in prompt
    assert prompt.rstrip().endswith("KB BODY")


# ── Tools ─────────────────────────────────────────────────────────────────────

def test_tool_query_brain_returns_llm_answer(server, monkeypatch):
    _write_brain(server, "ada", body="ada knowledge")
    captured = {}

    def fake_query(system_prompt, user_message):
        captured["system_prompt"] = system_prompt
        captured["user_message"] = user_message
        return "an answer"

    monkeypatch.setattr(server, "query_llm", fake_query)
    assert server.tool_query_brain("ada", "why?") == "an answer"
    assert "ada knowledge" in captured["system_prompt"]
    assert captured["user_message"] == "why?"


def test_tool_query_brain_unknown_brain_lists_available(server):
    result = server.tool_query_brain("nobody", "why?")
    assert "not found" in result
    assert "ada" in result and "grace" in result


def test_tool_query_brain_without_content_suggests_ingestion(server, monkeypatch):
    monkeypatch.setattr(server, "query_llm", lambda *a: pytest.fail("should not query"))
    assert "no knowledge yet" in server.tool_query_brain("ada", "why?")


def test_tool_cross_query_combines_brains_and_flags_gaps(server, monkeypatch):
    _write_brain(server, "ada", body="ada knowledge")
    monkeypatch.setattr(server, "query_llm", lambda system_prompt, question: "answer for " + question)

    result = server.tool_cross_query(["ada", "grace", "nobody"], "why?")

    assert "## Ada Lovelace" in result
    assert "answer for why?" in result
    assert "**Grace Hopper** — no knowledge yet" in result
    assert "**nobody** — not found" in result
    assert result.count("\n---\n") == 2


def test_tool_list_brains_renders_status_lines(server):
    _write_brain(server, "ada", entries=2)
    result = server.tool_list_brains()
    assert "- **ada** (Ada Lovelace) — 2 entries" in result
    assert "- **grace** (Grace Hopper) — no content yet" in result
    assert "Tags: math, computing" in result


def test_tool_list_brains_when_config_is_empty(server, monkeypatch):
    monkeypatch.setattr(server, "available_brains", lambda: [])
    assert "No brains configured yet" in server.tool_list_brains()


# ── MCP protocol ──────────────────────────────────────────────────────────────

def test_handle_mcp_request_initialize(server):
    response = server.handle_mcp_request("initialize", {}, 1)
    assert response["id"] == 1
    assert response["result"]["protocolVersion"] == server.MCP_PROTOCOL_VERSION
    assert response["result"]["serverInfo"]["name"] == "brain-mcp-server"


def test_handle_mcp_request_initialized_notification_has_no_response(server):
    assert server.handle_mcp_request("notifications/initialized", {}, None) is None


def test_handle_mcp_request_tools_list_exposes_three_tools(server):
    tools = server.handle_mcp_request("tools/list", {}, 2)["result"]["tools"]
    assert [t["name"] for t in tools] == ["query_brain", "cross_query", "list_brains"]
    assert tools[0]["inputSchema"]["required"] == ["brain", "question"]


def test_handle_mcp_request_unknown_method_returns_method_not_found(server):
    error = server.handle_mcp_request("does/notexist", {}, 3)["error"]
    assert error["code"] == -32601
    assert "does/notexist" in error["message"]


@pytest.mark.parametrize(
    "tool_name,arguments,stub_attr",
    [
        ("query_brain", {"brain": "ada", "question": "q"}, "tool_query_brain"),
        ("cross_query", {"brains": ["ada"], "question": "q"}, "tool_cross_query"),
        ("list_brains", {}, "tool_list_brains"),
    ],
)
def test_handle_mcp_request_tools_call_dispatches(server, monkeypatch, tool_name, arguments, stub_attr):
    monkeypatch.setattr(server, stub_attr, lambda *a, **k: f"{tool_name} result")
    response = server.handle_mcp_request("tools/call", {"name": tool_name, "arguments": arguments}, 4)
    assert response["result"]["content"] == [{"type": "text", "text": f"{tool_name} result"}]
    assert response["result"]["isError"] is False


def test_handle_mcp_request_tools_call_unknown_tool(server):
    response = server.handle_mcp_request("tools/call", {"name": "nope"}, 5)
    assert response["result"]["content"][0]["text"] == "Unknown tool: nope"


def test_handle_mcp_request_tools_call_reports_tool_exception(server, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(server, "tool_list_brains", boom)
    response = server.handle_mcp_request("tools/call", {"name": "list_brains"}, 6)
    assert "Error executing list_brains: kaboom" in response["result"]["content"][0]["text"]


def test_handle_mcp_request_tools_call_missing_argument_is_reported(server):
    response = server.handle_mcp_request("tools/call", {"name": "query_brain", "arguments": {}}, 7)
    assert "Error executing query_brain" in response["result"]["content"][0]["text"]


# ── HTTP routes ───────────────────────────────────────────────────────────────

def test_head_root_advertises_protocol_version(client, server):
    response = client.head("/")
    assert response.status_code == 200
    assert response.headers["MCP-Protocol-Version"] == server.MCP_PROTOCOL_VERSION


def test_post_initialize_needs_no_auth(client, server, monkeypatch):
    monkeypatch.setattr(server, "MCP_AUTH_TOKEN", "secret")
    response = client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response.status_code == 200
    assert response.json()["result"]["protocolVersion"] == server.MCP_PROTOCOL_VERSION


def test_post_notification_returns_204(client):
    response = client.post("/", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status_code == 204


def test_post_tools_call_requires_auth(client, server, monkeypatch):
    monkeypatch.setattr(server, "MCP_AUTH_TOKEN", "secret")
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}

    assert client.post("/", json=body).status_code == 401
    ok = client.post("/", json=body, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    assert len(ok.json()["result"]["tools"]) == 3


def test_health_reports_brain_counts(client, server):
    _write_brain(server, "ada")
    assert client.get("/health").json() == {
        "status": "ok",
        "brains": 2,
        "brains_with_content": 1,
    }
