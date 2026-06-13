"""Optional Anthropic Claude extractor provider.

Environment variables:

    ANTHROPIC_API_KEY   required for provider=anthropic
    ANTHROPIC_MODEL     optional, defaults to claude-sonnet-4-20250514

The provider calls Anthropic's Messages API directly, keeping this project free
from an SDK dependency.
"""
from __future__ import annotations

import json
import os
import urllib.request

from ripplekg.extraction.openai_provider import _coerce_triples
from ripplekg.extraction.openai_provider import _extract_json
from ripplekg.extraction.openai_provider import _prompt
from ripplekg.models import Triple


def _content_text(payload: dict) -> str:
    chunks = []
    for item in payload.get("content", []):
        if item.get("type") == "text":
            chunks.append(item.get("text", ""))
    return "\n".join(chunks).strip()


def build_edit_with_anthropic(
    sentence_text: str,
    current_triples: list[Triple],
    instruction: str,
    input_kind: str = "instruction",
) -> tuple[str, list[Triple], bool]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required when provider='anthropic'")

    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    body = {
        "model": model,
        "max_tokens": 800,
        "temperature": 0,
        "system": "You produce strict JSON for KG sentence edit extraction.",
        "messages": [
            {
                "role": "user",
                "content": _prompt(sentence_text, current_triples, instruction, input_kind),
            },
        ],
    }
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))

    parsed = _extract_json(_content_text(payload))
    new_text = str(parsed.get("new_text", "")).strip()
    if not new_text:
        raise ValueError("Claude response missing non-empty new_text")
    applies = bool(parsed.get("applies_to_sentence", input_kind != "fact"))
    return new_text, _coerce_triples(parsed.get("intended_triples")), applies
