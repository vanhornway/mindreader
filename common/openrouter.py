"""Shared OpenRouter chat-completion client used by ingestion and the MCP server."""

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def openrouter_chat(
    api_key: str,
    model: str,
    messages: list[dict],
    *,
    max_tokens: int,
    temperature: float,
    timeout: int = 30,
) -> str:
    """Call the OpenRouter chat-completions API and return the response text.

    Raises on HTTP or response-shape errors; callers decide how to fall back.
    """
    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
