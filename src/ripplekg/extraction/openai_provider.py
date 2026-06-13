"""Optional OpenAI-compatible extractor provider.

This module is intentionally isolated so the core project has no hard runtime
dependency on an LLM SDK. It uses environment variables:

    OPENAI_API_KEY   required for provider=openai
    OPENAI_MODEL     optional, defaults to gpt-4o-mini

The provider returns EditOp ingredients plus a relevance decision:
``new_text``, ``intended_triples``, and ``applies_to_sentence``.
"""
from __future__ import annotations

import json
import os
import urllib.request

from ripplekg.models import Triple


def _prompt(
    sentence_text: str,
    current_triples: list[Triple],
    instruction: str,
    input_kind: str = "instruction",
) -> str:
    triples = "\n".join(f"- {h} | {r} | {t}" for h, r, t in current_triples) or "(none)"
    if input_kind == "fact":
        request_label = "Updated fact"
        request_rule = (
            "The user supplied an updated fact, not a text-edit command. "
            "Set applies_to_sentence=true only if the original sentence directly "
            "discusses the same subject and fact/topic, contradicts the updated fact, "
            "or is a natural evidence sentence for that fact. Sharing only an entity "
            "or a broad topic is not enough. If false, return the original sentence "
            "and current triples unchanged. Never append an unrelated fact."
        )
    else:
        request_label = "Edit instruction"
        request_rule = "Follow the edit instruction."

    return f"""You are editing a sentence used as evidence for a knowledge graph.

Return JSON only with this schema:
{{
  "applies_to_sentence": true,
  "new_text": "edited sentence",
  "intended_triples": [["head", "relation", "tail"]]
}}

Rules:
- {request_rule}
- For edit instructions, set applies_to_sentence=true.
- The edited sentence must remain grammatical, fluent, and natural.
- You may rewrite nearby wording to preserve grammar after changing the sentence.
- Keep relation names exactly as provided when a triple remains true.
- Include every current triple that is still supported by the edited sentence.
- Remove triples no longer supported by the edited sentence.
- Add a triple only if the edited sentence explicitly supports it.
- Do not include explanations outside JSON.

Original sentence:
{sentence_text}

Current triples:
{triples}

{request_label}:
{instruction}
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("LLM response did not contain a JSON object")
    return json.loads(text[start:end + 1])


def _coerce_triples(value) -> list[Triple]:
    triples = []
    for item in value or []:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            continue
        head, rel_type, tail = (str(part).strip() for part in item)
        if head and rel_type and tail:
            triples.append((head, rel_type, tail))
    return triples


def build_edit_with_openai(
    sentence_text: str,
    current_triples: list[Triple],
    instruction: str,
    input_kind: str = "instruction",
) -> tuple[str, list[Triple], bool]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required when provider='openai'")

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You produce strict JSON for KG sentence edit extraction.",
            },
            {
                "role": "user",
                "content": _prompt(sentence_text, current_triples, instruction, input_kind),
            },
        ],
        "temperature": 0,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))

    content = payload["choices"][0]["message"]["content"]
    parsed = _extract_json(content)
    new_text = str(parsed.get("new_text", "")).strip()
    if not new_text:
        raise ValueError("LLM response missing non-empty new_text")
    applies = bool(parsed.get("applies_to_sentence", input_kind != "fact"))
    return new_text, _coerce_triples(parsed.get("intended_triples")), applies
