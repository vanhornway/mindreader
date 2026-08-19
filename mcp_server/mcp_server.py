#!/usr/bin/env python3
"""
Brain MCP Server
================
A FastAPI-based MCP (Model Context Protocol) server that exposes three tools
to Claude.ai:

  - query_brain       Ask a specific brain a question
  - cross_query       Ask multiple brains the same question
  - list_brains       List available brains and their tags

Register this server at: claude.ai → Settings → Connectors → Add custom integration

Transport: Streamable HTTP (MCP spec 2025-06-18)
Auth: Bearer token (simple, no OAuth needed for personal use)
"""

import os
import json
import uuid
import logging
from pathlib import Path
from typing import Any

import yaml
import requests
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import StreamingResponse
import uvicorn

# ── Config ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

ROOT         = Path(__file__).parent.parent
CONFIG_FILE  = Path("/app/brains.yaml")
BRAINS_DIR   = Path("/app/brains")

OPENROUTER_KEY   = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-3-5-haiku")
MCP_AUTH_TOKEN   = os.environ.get("MCP_AUTH_TOKEN", "")   # your secret token
MCP_PROTOCOL_VERSION = "2025-06-18"

# Max chars of brain content to include in each query (context window budget)
MAX_BRAIN_CHARS = 80_000

app = FastAPI(title="Brain MCP Server", version="1.0.0")


class ToolError(Exception):
    """A tool could not complete. Surfaced to the client with isError=True."""


class InvalidParams(Exception):
    """Tool arguments were missing or the wrong type (JSON-RPC -32602)."""


@app.on_event("startup")
async def warn_on_missing_auth():
    if not MCP_AUTH_TOKEN:
        log.warning("MCP_AUTH_TOKEN is not set — every request is accepted unauthenticated")

# ── Auth ──────────────────────────────────────────────────────────────────────

def verify_auth(request: Request):
    """Simple bearer token auth. Skip auth for MCP handshake endpoints."""
    if not MCP_AUTH_TOKEN:
        return  # no auth configured — allow all (only do this locally)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != MCP_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

# ── Brain loading ─────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        config = yaml.safe_load(CONFIG_FILE.read_text())
    except FileNotFoundError as e:
        raise ToolError(f"Server misconfigured: {CONFIG_FILE} not found") from e
    except OSError as e:
        raise ToolError(f"Server could not read {CONFIG_FILE}: {e}") from e
    except yaml.YAMLError as e:
        raise ToolError(f"Server config {CONFIG_FILE} is not valid YAML: {e}") from e
    if not isinstance(config, dict) or not isinstance(config.get("brains"), dict):
        raise ToolError(f"Server config {CONFIG_FILE} must define a top-level 'brains' mapping")
    return config["brains"]

def load_brain_content(brain_slug: str) -> str:
    """Load brain .md file content, truncated to fit context window.

    Returns "" only when the brain genuinely has no knowledge file; read errors
    raise so they aren't reported to the user as "no knowledge yet".
    """
    brain_file = BRAINS_DIR / f"{brain_slug}.md"
    if not brain_file.exists():
        return ""
    try:
        content = brain_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ToolError(f"Could not read knowledge file for '{brain_slug}': {e}") from e
    if len(content) > MAX_BRAIN_CHARS:
        # Keep header + most recent entries (file is append-only so newest are at end)
        marker = content.find("---\n\n")
        header_end = marker + 5 if marker != -1 else 0
        header = content[:header_end]
        rest = content[header_end:]
        # Take last MAX_BRAIN_CHARS worth of entries
        trimmed = rest[-(MAX_BRAIN_CHARS - len(header)):]
        content = header + "\n[...earlier entries trimmed for context...]\n\n" + trimmed
    return content

def available_brains() -> list[dict]:
    """Return list of brains that have a knowledge file."""
    config = load_config()
    result = []
    for slug, cfg in config.items():
        if "display_name" not in cfg or "expertise_tags" not in cfg:
            raise ToolError(f"Brain '{slug}' in {CONFIG_FILE} is missing display_name/expertise_tags")
        brain_file = BRAINS_DIR / f"{slug}.md"
        entry_count = 0
        if brain_file.exists():
            try:
                entry_count = brain_file.read_text(encoding="utf-8").count("\n## ")
            except (OSError, UnicodeDecodeError) as e:
                raise ToolError(f"Could not read knowledge file for '{slug}': {e}") from e
        result.append({
            "slug": slug,
            "display_name": cfg["display_name"],
            "tags": cfg["expertise_tags"],
            "has_content": brain_file.exists(),
            "entry_count": entry_count,
        })
    return result

# ── LLM query ─────────────────────────────────────────────────────────────────

def query_llm(system_prompt: str, user_message: str) -> str:
    """Send a query to OpenRouter and return the response text.

    Raises ToolError on failure: returning the error as the answer text made the
    client treat upstream failures as successful tool results.
    """
    if not OPENROUTER_KEY:
        raise ToolError("OPENROUTER_API_KEY is not configured on the server.")
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "max_tokens": 1500,
                "temperature": 0.7,
            },
            timeout=45,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except requests.RequestException as e:
        log.error(f"OpenRouter request failed: {e}")
        raise ToolError(f"Upstream model request failed: {e}") from e
    except (ValueError, KeyError, IndexError, TypeError) as e:
        log.error(f"Unexpected OpenRouter response: {e!r}")
        raise ToolError(f"Unexpected response from upstream model: {e!r}") from e
    if not content:
        raise ToolError("Upstream model returned an empty response.")
    return content

def build_system_prompt(brain_slug: str, brain_config: dict, brain_content: str) -> str:
    return f"""You are responding as a knowledge assistant with deep expertise in {brain_config['display_name']}'s published work and thinking.

STYLE GUIDE:
{brain_config['style_notes'].strip()}

INSTRUCTIONS:
- Answer ONLY from the knowledge base provided below. Do not invent claims or opinions not present in the source material.
- Respond in the style and voice described above.
- If the knowledge base doesn't contain enough information to answer the question, say so clearly.
- Cite specific sources where relevant (e.g. "In his video on X..." or "From his newsletter on Y...").
- Be direct and substantive. Don't hedge excessively.

KNOWLEDGE BASE — {brain_config['display_name']}:
{brain_content}
"""

# ── MCP tool implementations ──────────────────────────────────────────────────

def tool_query_brain(brain: str, question: str) -> str:
    config = load_config()
    if brain not in config:
        raise InvalidParams(f"brain '{brain}' not found. Available: {', '.join(config.keys())}")
    brain_config = config[brain]
    brain_content = load_brain_content(brain)
    if not brain_content:
        raise ToolError(f"Brain '{brain}' exists in config but has no knowledge yet. Run the ingestion script first.")
    system_prompt = build_system_prompt(brain, brain_config, brain_content)
    return query_llm(system_prompt, question)

def tool_cross_query(brains: list[str], question: str) -> str:
    if not brains:
        raise InvalidParams("'brains' must contain at least one brain slug")
    config = load_config()
    responses = []
    failures = 0
    for brain_slug in brains:
        if brain_slug not in config:
            responses.append(f"**{brain_slug}** — not found\n")
            continue
        brain_config = config[brain_slug]
        brain_content = load_brain_content(brain_slug)
        if not brain_content:
            responses.append(f"**{brain_config['display_name']}** — no knowledge yet\n")
            continue
        system_prompt = build_system_prompt(brain_slug, brain_config, brain_content)
        try:
            answer = query_llm(system_prompt, question)
        except ToolError as e:
            # One failing brain shouldn't discard the answers already gathered.
            failures += 1
            log.error(f"cross_query failed for '{brain_slug}': {e}")
            responses.append(f"## {brain_config['display_name']}\n\nQuery failed: {e}\n")
            continue
        responses.append(f"## {brain_config['display_name']}\n\n{answer}\n")
    if failures and failures == len(brains):
        raise ToolError(f"All {failures} brain queries failed. Last error above.")
    return "\n---\n\n".join(responses)

def tool_list_brains() -> str:
    brains = available_brains()
    if not brains:
        return "No brains configured yet. Add entries to brains.yaml and run ingest.py."
    lines = ["Available knowledge brains:\n"]
    for b in brains:
        status = f"{b['entry_count']} entries" if b["has_content"] else "no content yet"
        lines.append(f"- **{b['slug']}** ({b['display_name']}) — {status}")
        lines.append(f"  Tags: {', '.join(b['tags'])}")
    return "\n".join(lines)

# ── MCP tool registry ─────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "query_brain",
        "description": (
            "Query a specific person's knowledge brain and get a response in their style. "
            "Use this when the user wants to ask Nate, Dr. Berg, Andrew Ng, or any other "
            "configured brain a question. The response is grounded in that person's actual "
            "published content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "brain": {
                    "type": "string",
                    "description": "Brain slug (e.g. 'nate_jones', 'dr_berg', 'andrew_ng'). Use list_brains to see all options.",
                },
                "question": {
                    "type": "string",
                    "description": "The question to ask this brain.",
                },
            },
            "required": ["brain", "question"],
        },
    },
    {
        "name": "cross_query",
        "description": (
            "Ask the same question to multiple brains and get a comparative response. "
            "Useful for 'what would X and Y say about Z?' style questions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "brains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of brain slugs to query (e.g. ['nate_jones', 'andrew_ng'])",
                },
                "question": {
                    "type": "string",
                    "description": "The question to ask all selected brains.",
                },
            },
            "required": ["brains", "question"],
        },
    },
    {
        "name": "list_brains",
        "description": "List all available knowledge brains, their expertise tags, and how many entries they contain.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

# ── MCP protocol handlers ─────────────────────────────────────────────────────

def jsonrpc_error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def require_arg(arguments: dict, name: str, expected_type: type):
    """Fetch a tool argument, raising InvalidParams if absent or mistyped."""
    if name not in arguments:
        raise InvalidParams(f"missing required argument '{name}'")
    value = arguments[name]
    if not isinstance(value, expected_type):
        raise InvalidParams(
            f"argument '{name}' must be {expected_type.__name__}, got {type(value).__name__}"
        )
    return value


def handle_mcp_request(method: str, params: dict, request_id: Any) -> dict:
    """Route an MCP JSON-RPC method to the right handler."""

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "brain-mcp-server", "version": "1.0.0"},
            },
        }

    elif method == "notifications/initialized":
        return None  # notification, no response needed

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": TOOLS},
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        log.info(f"Tool call: {tool_name}({arguments})")

        is_error = False
        try:
            if tool_name == "query_brain":
                result = tool_query_brain(require_arg(arguments, "brain", str),
                                          require_arg(arguments, "question", str))
            elif tool_name == "cross_query":
                result = tool_cross_query(require_arg(arguments, "brains", list),
                                          require_arg(arguments, "question", str))
            elif tool_name == "list_brains":
                result = tool_list_brains()
            else:
                return jsonrpc_error(request_id, -32602, f"Unknown tool: {tool_name}")
        except InvalidParams as e:
            return jsonrpc_error(request_id, -32602, f"Invalid arguments for {tool_name}: {e}")
        except ToolError as e:
            log.error(f"Tool {tool_name} failed: {e}")
            result, is_error = f"Error executing {tool_name}: {e}", True
        except Exception as e:
            log.error(f"Unexpected error in {tool_name}: {e}", exc_info=True)
            result, is_error = f"Error executing {tool_name}: {e}", True

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": result}],
                "isError": is_error,
            },
        }

    else:
        return jsonrpc_error(request_id, -32601, f"Method not found: {method}")

# ── FastAPI routes ────────────────────────────────────────────────────────────

@app.head("/")
async def head_root():
    """MCP protocol discovery endpoint."""
    return Response(
        headers={"MCP-Protocol-Version": MCP_PROTOCOL_VERSION},
        status_code=200,
    )

@app.post("/")
async def mcp_endpoint(request: Request):
    """Main MCP endpoint — handles all JSON-RPC methods."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # Without this the client gets an HTML/plain 500 instead of JSON-RPC.
        return jsonrpc_error(None, -32700, f"Parse error: {e}")
    if not isinstance(body, dict):
        return jsonrpc_error(None, -32600, "Invalid Request: body must be a JSON object")

    method = body.get("method", "")
    params = body.get("params") or {}
    request_id = body.get("id")
    if not isinstance(params, dict):
        return jsonrpc_error(request_id, -32602, "Invalid params: 'params' must be an object")

    # Auth check — skip for initialize (per MCP spec, no token on first handshake)
    if method not in ("initialize", "notifications/initialized"):
        verify_auth(request)

    try:
        response = handle_mcp_request(method, params, request_id)
    except Exception as e:
        log.error(f"Unhandled error for method {method}: {e}", exc_info=True)
        return jsonrpc_error(request_id, -32603, f"Internal error: {e}")

    if response is None:
        # Notification — return 204
        return Response(status_code=204)

    return response

@app.get("/health")
async def health():
    try:
        brains = available_brains()
    except ToolError as e:
        log.error(f"Health check failed: {e}")
        return Response(
            content=json.dumps({"status": "error", "detail": str(e)}),
            media_type="application/json",
            status_code=503,
        )
    return {
        "status": "ok",
        "brains": len(brains),
        "brains_with_content": sum(1 for b in brains if b["has_content"]),
    }

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("mcp_server:app", host="0.0.0.0", port=port, reload=False)
