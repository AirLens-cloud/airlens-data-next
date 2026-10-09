#!/usr/bin/env python3
"""
llm_extract.py — OpenAI gpt-4o-mini JSON extraction (port of _shared/openai.ts chatCompleteJson).

urllib only — no `openai` SDK dependency. Opt-in module: the default $0 / LLM-0
cron never imports this (gated behind RUN_LLM_EXTRACT). Requires OPENAI_API_KEY.

Provider-swappable like openai.ts: OPENAI_API_BASE + LLM_EXTRACT_MODEL env let an
OpenAI-compatible endpoint (e.g. a free-tier gateway) replace the default.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("LLM_EXTRACT_MODEL", "gpt-4o-mini")
OPENAI_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com")
REQUEST_TIMEOUT = 30


class LLMExtractError(Exception):
    """Raised when the LLM call fails or returns non-JSON content."""


def parse_json_loose(content: str | None) -> dict | None:
    """Parse JSON, tolerating code fences / surrounding prose (cloudflareAI.ts:parseJsonLoose port)."""
    if not content:
        return None
    # strict first
    try:
        return json.loads(content)
    except Exception:  # noqa: BLE001
        pass
    # strip ```json ... ``` fences
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:  # noqa: BLE001
            pass
    # first { ... last }
    start, end = content.find("{"), content.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(content[start:end + 1])
        except Exception:  # noqa: BLE001
            pass
    return None


def chat_complete_json(
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 800,
    temperature: float = 0.2,
    retries: int = 1,
) -> dict:
    """Call OpenAI chat completions in JSON mode and return the parsed object.

    Raises LLMExtractError if the key is missing or the response is unusable.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise LLMExtractError("OPENAI_API_KEY not set")

    model = model or DEFAULT_MODEL
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    url = f"{OPENAI_BASE}/v1/chat/completions"

    last_err: Exception | None = None
    for _attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                data = json.load(resp)
            content = data["choices"][0]["message"]["content"]
            parsed = parse_json_loose(content)
            if parsed is None:
                raise LLMExtractError("OpenAI returned non-JSON content")
            return parsed
        except LLMExtractError:
            raise
        except Exception as e:  # noqa: BLE001 — transient; retry then surface
            last_err = e
            continue
    raise LLMExtractError(f"OpenAI request failed: {last_err}")
