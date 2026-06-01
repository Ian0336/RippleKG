"""Generate edited sentences and intended triples before M1/M2.

This is the A -> C handoff:

    corpus sentence + edit instruction
      -> EditOp(new_text, intended_triples)
      -> mechanism.pipeline.run_edit_transactional(...)

The heuristic provider is deterministic and works offline. The OpenAI provider
is optional and only used when explicitly requested.
"""
from __future__ import annotations

import re

from arango.database import StandardDatabase

from ripplekg.db import repo
from ripplekg.ingest.docred import normalize_name
from ripplekg.models import EditOp, Triple


def current_triples_for_sentence(db: StandardDatabase, sent_id: str) -> list[Triple]:
    """Return active relation triples currently supported by one sentence."""
    affected = repo.affected_evidence(db, sent_id)
    triples = []
    for item in affected["relations"]:
        relation = repo.get_relation(db, item["relation_id"])
        if relation is None:
            continue
        head = repo.get_entity(db, relation["head"])
        tail = repo.get_entity(db, relation["tail"])
        if head is None or tail is None:
            continue
        triples.append((head["name"], relation["rel_type"], tail["name"]))
    return triples


def get_sentence(db: StandardDatabase, doc_id: str, sent_idx: int) -> dict:
    sent_id = f"{doc_id}:{sent_idx}"
    col = db.collection("sentences")
    if not col.has(sent_id):
        raise ValueError(f"sentence not found: {sent_id}")
    return col.get(sent_id)


def heuristic_intended_triples(current_triples: list[Triple], new_text: str) -> list[Triple]:
    """Conservative offline extractor.

    Keep a triple only when both endpoint names still appear in the edited
    sentence. This is not a replacement for LLM/IE extraction; it provides a
    deterministic fallback for tests, demos, and no-key environments.
    """
    normalized_text = normalize_name(new_text)
    intended = []
    for head, rel_type, tail in current_triples:
        if normalize_name(head) in normalized_text and normalize_name(tail) in normalized_text:
            intended.append((head, rel_type, tail))
    return intended


def verify_supported_old_triples(
    current_triples: list[Triple],
    new_text: str,
    candidate_triples: list[Triple],
) -> list[Triple]:
    """Merge extractor candidates with old triples still supported by text.

    This is the deterministic guardrail that matches the project design:
    the LLM/IE provider proposes a new evidence set, then a semantic verifier
    checks whether old evidence remains supported by the edited sentence.

    The current implementation uses conservative surface-form entailment:
    if both old triple endpoints still appear in the edited sentence, keep the
    old triple even if the extractor omitted it. Relation names are kept from
    the original KG, so M1 can compare canonical triples reliably.
    """
    normalized_text = normalize_name(new_text)
    verified = list(candidate_triples)
    seen = set(verified)

    for triple in current_triples:
        head, _, tail = triple
        still_supported = (
            normalize_name(head) in normalized_text
            and normalize_name(tail) in normalized_text
        )
        if still_supported and triple not in seen:
            verified.append(triple)
            seen.add(triple)

    return verified


def _cleanup_text(text: str) -> str:
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\(\s*\)", "", text).strip()
    return text


def _remove_phrase(text: str, phrase: str) -> str:
    pattern = r"(?<!\w)\s*,?\s*" + re.escape(phrase) + r"\s*,?(?!\w)"
    return _cleanup_text(re.sub(pattern, " ", text, count=1))


def _replace_phrase(text: str, old: str, new: str) -> str:
    pattern = r"(?<!\w)" + re.escape(old) + r"(?!\w)"
    return _cleanup_text(re.sub(pattern, new, text, count=1))


def heuristic_rewrite(sentence: str, instruction: str) -> str:
    """Small deterministic edit generator for controlled demos.

    Supported instructions:
    - "remove <phrase>"
    - "delete <phrase>"
    - "replace <old> with <new>"

    Other instructions leave the text unchanged, so the extractor can still
    produce an EditOp safely.
    """
    stripped = instruction.strip()

    replace = re.search(r"\breplace\s+(.+?)\s+with\s+(.+)$", stripped, flags=re.I)
    if replace:
        old, new = replace.group(1).strip(), replace.group(2).strip()
        return _replace_phrase(sentence, old, new)

    remove = re.search(r"\b(?:remove|delete|drop)\s+(.+)$", stripped, flags=re.I)
    if remove:
        phrase = remove.group(1).strip()
        return _remove_phrase(sentence, phrase)

    return sentence


def build_edit_from_instruction(
    db: StandardDatabase,
    doc_id: str,
    sent_idx: int,
    instruction: str,
    provider: str = "heuristic",
) -> EditOp:
    """Build an EditOp from a natural-language edit instruction."""
    sentence = get_sentence(db, doc_id, sent_idx)
    sent_id = sentence["_key"]
    current_triples = current_triples_for_sentence(db, sent_id)

    if provider == "heuristic":
        new_text = heuristic_rewrite(sentence["text"], instruction)
        intended = heuristic_intended_triples(current_triples, new_text)
    elif provider == "anthropic":
        from ripplekg.extraction.anthropic_provider import build_edit_with_anthropic

        new_text, intended = build_edit_with_anthropic(
            sentence_text=sentence["text"],
            current_triples=current_triples,
            instruction=instruction,
        )
    elif provider == "openai":
        from ripplekg.extraction.openai_provider import build_edit_with_openai

        new_text, intended = build_edit_with_openai(
            sentence_text=sentence["text"],
            current_triples=current_triples,
            instruction=instruction,
        )
    else:
        raise ValueError("provider must be 'heuristic', 'anthropic', or 'openai'")

    intended = verify_supported_old_triples(current_triples, new_text, intended)

    return EditOp(
        doc_id=doc_id,
        sent_idx=sent_idx,
        new_text=new_text,
        intended_triples=intended,
    )
