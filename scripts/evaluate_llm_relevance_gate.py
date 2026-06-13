"""Evaluate an LLM relevance gate against reviewed should-edit annotations."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

from ripplekg.extraction.anthropic_provider import _content_text
from ripplekg.extraction.openai_provider import _extract_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", default="data/edit_annotation_set.json")
    parser.add_argument("--output", default="data/llm_relevance_gate_eval.json")
    parser.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--refresh", action="store_true", help="Ignore cached decisions.")
    return parser.parse_args()


def prompt_for_case(case: dict[str, Any]) -> str:
    candidates = [
        {
            "sentence_id": candidate["sentence_id"],
            "text": candidate["text"],
        }
        for candidate in case["candidates"]
    ]
    return f"""You are a relevance gate for document-level fact replacement.

The update explicitly replaces the old fact with the new fact.
For each candidate sentence, decide whether that sentence itself must be edited
because it states or structurally expresses the old fact and would become stale.

Important rules:
- Sharing an entity, location, or broad topic is not sufficient.
- Do not infer that a sentence needs editing merely from external world knowledge.
- A sentence that only indirectly suggests the old fact should be false.
- A plain list does not assert follows/followed-by relationships merely because
  two items appear next to each other.
- Return exactly one decision for every candidate sentence.

Return JSON only:
{{
  "decisions": [
    {{"sentence_id": "doc0:0", "should_edit": true, "reason": "short reason"}}
  ]
}}

Old fact:
{case["old_fact"]}

New fact:
{case["new_fact"]}

Candidates:
{json.dumps(candidates, ensure_ascii=False)}
"""


def call_anthropic(prompt: str) -> dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required")
    body = {
        "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        "max_tokens": 1400,
        "temperature": 0,
        "system": "You produce strict JSON relevance decisions.",
        "messages": [{"role": "user", "content": prompt}],
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
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return _extract_json(_content_text(payload))


def call_openai(prompt: str) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")
    body = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "You produce strict JSON relevance decisions."},
            {"role": "user", "content": prompt},
        ],
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
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return _extract_json(payload["choices"][0]["message"]["content"])


def normalize_decisions(case: dict[str, Any], response: dict[str, Any]) -> list[dict[str, Any]]:
    candidate_ids = {candidate["sentence_id"] for candidate in case["candidates"]}
    decisions_by_id = {}
    for decision in response.get("decisions", []):
        sentence_id = str(decision.get("sentence_id", ""))
        if sentence_id in candidate_ids:
            decisions_by_id[sentence_id] = {
                "sentence_id": sentence_id,
                "should_edit": bool(decision.get("should_edit", False)),
                "reason": str(decision.get("reason", "")).strip(),
            }
    missing = candidate_ids - set(decisions_by_id)
    if missing:
        raise ValueError(f"LLM response missing decisions for: {sorted(missing)}")
    return [decisions_by_id[candidate["sentence_id"]] for candidate in case["candidates"]]


def load_cache(path: Path, provider: str, refresh: bool) -> dict[str, Any]:
    if not refresh and path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("provider") == provider:
            return payload
    return {"provider": provider, "cases": {}}


def save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def call_with_retries(call_provider: Any, prompt: str, retries: int) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return call_provider(prompt)
        except Exception as exc:  # noqa: BLE001 - network/API failures vary
            last_error = exc
            if attempt == retries:
                break
            delay = attempt * 2
            print(
                f"  attempt {attempt}/{retries} failed: {type(exc).__name__}; "
                f"retrying in {delay}s",
                flush=True,
            )
            time.sleep(delay)
    raise RuntimeError(f"LLM call failed after {retries} attempts") from last_error


def calculate_metrics(cases: list[dict[str, Any]], cache: dict[str, Any]) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    evaluated_cases = 0
    for case in cases:
        result = cache["cases"].get(case["case_id"])
        if not result:
            continue
        evaluated_cases += 1
        expected = {
            candidate["sentence_id"]: candidate["human_should_edit"]
            for candidate in case["candidates"]
        }
        for decision in result["decisions"]:
            predicted = decision["should_edit"]
            actual = expected[decision["sentence_id"]]
            tp += predicted and actual
            fp += predicted and not actual
            fn += not predicted and actual
            tn += not predicted and not actual
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / (tp + fp + fn + tn) if tp + fp + fn + tn else 0.0
    return {
        "evaluated_cases": evaluated_cases,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


def main() -> None:
    args = parse_args()
    annotation_payload = json.loads(Path(args.annotations).read_text(encoding="utf-8"))
    cases = annotation_payload["cases"]
    if args.max_cases is not None:
        cases = cases[:args.max_cases]
    if any(
        not isinstance(candidate.get("human_should_edit"), bool)
        for case in cases
        for candidate in case["candidates"]
    ):
        raise SystemExit("Complete human_should_edit annotations before evaluating the gate.")

    output = Path(args.output)
    cache = load_cache(output, args.provider, args.refresh)
    call_provider = call_anthropic if args.provider == "anthropic" else call_openai

    for index, case in enumerate(cases, start=1):
        if case["case_id"] in cache["cases"]:
            print(f"[{index}/{len(cases)}] cached {case['case_id']}", flush=True)
            continue
        print(
            f"[{index}/{len(cases)}] calling {args.provider} for {case['case_id']}",
            flush=True,
        )
        response = call_with_retries(call_provider, prompt_for_case(case), args.retries)
        cache["cases"][case["case_id"]] = {
            "old_fact": case["old_fact"],
            "new_fact": case["new_fact"],
            "decisions": normalize_decisions(case, response),
        }
        save_cache(output, cache)

    cache["metrics"] = calculate_metrics(cases, cache)
    save_cache(output, cache)
    metrics = cache["metrics"]
    print()
    print(f"provider={args.provider}")
    print(f"evaluated_cases={metrics['evaluated_cases']}")
    print(
        f"precision={metrics['precision']:.3f} recall={metrics['recall']:.3f} "
        f"f1={metrics['f1']:.3f} accuracy={metrics['accuracy']:.3f}"
    )
    print(f"tp={metrics['tp']} fp={metrics['fp']} fn={metrics['fn']} tn={metrics['tn']}")
    print(f"output={output}")


if __name__ == "__main__":
    main()
