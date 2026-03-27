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
    return yaml.safe_load(CONFIG_FILE.read_text())["brains"]

def load_brain_content(brain_slug: str) -> str:
    """Load brain .md file content, truncated to fit context window."""
    brain_file = BRAINS_DIR / f"{brain_slug}.md"
    if not brain_file.exists():
        return ""
    content = brain_file.read_text(encoding="utf-8")
    if len(content) > MAX_BRAIN_CHARS:
        # Keep header + most recent entries (file is append-only so newest are at end)
        header_end = content.find("---\n\n") + 5
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
        brain_file = BRAINS_DIR / f"{slug}.md"
        entry_count = 0
        if brain_file.exists():
            entry_count = brain_file.read_text().count("\n## ")
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
    """Send a query to OpenRouter and return the response text."""
    if not OPENROUTER_KEY:
        return "Error: OPENROUTER_API_KEY not configured on the server."
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
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.error(f"OpenRouter error: {e}")
        return f"Error querying LLM: {e}"

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
        return f"Brain '{brain}' not found. Available: {', '.join(config.keys())}"
    brain_config = config[brain]
    brain_content = load_brain_content(brain)
    if not brain_content:
        return f"Brain '{brain}' exists in config but has no knowledge yet. Run the ingestion script first."
    system_prompt = build_system_prompt(brain, brain_config, brain_content)
    return query_llm(system_prompt, question)

def tool_cross_query(brains: list[str], question: str) -> str:
    config = load_config()
    responses = []
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
        answer = query_llm(system_prompt, question)
        responses.append(f"## {brain_config['display_name']}\n\n{answer}\n")
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

        try:
            if tool_name == "query_brain":
                result = tool_query_brain(arguments["brain"], arguments["question"])
            elif tool_name == "cross_query":
                result = tool_cross_query(arguments["brains"], arguments["question"])
            elif tool_name == "list_brains":
                result = tool_list_brains()
            else:
                result = f"Unknown tool: {tool_name}"
        except Exception as e:
            log.error(f"Tool error: {e}", exc_info=True)
            result = f"Error executing {tool_name}: {e}"

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": result}],
                "isError": False,
            },
        }

    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

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
    body = await request.json()
    method = body.get("method", "")
    params = body.get("params", {})
    request_id = body.get("id")

    # Auth check — skip for initialize (per MCP spec, no token on first handshake)
    if method not in ("initialize", "notifications/initialized"):
        verify_auth(request)

    response = handle_mcp_request(method, params, request_id)

    if response is None:
        # Notification — return 204
        return Response(status_code=204)

    return response

@app.get("/health")
async def health():
    brains = available_brains()
    return {
        "status": "ok",
        "brains": len(brains),
        "brains_with_content": sum(1 for b in brains if b["has_content"]),
    }

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("mcp_server:app", host="0.0.0.0", port=port, reload=False)
