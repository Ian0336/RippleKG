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
from ripplekg.extraction.schema_merge import canonicalize_triples
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


def _instruction_focus_terms(instruction: str) -> list[str]:
    """Extract conservative surface terms that identify related sentences."""
    stripped = instruction.strip()
    terms = []

    replace = re.search(r"\breplace\s+(.+?)\s+with\s+(.+)$", stripped, flags=re.I)
    if replace:
        terms.append(replace.group(1).strip())

    remove = re.search(r"\b(?:remove|delete|drop)\s+(.+)$", stripped, flags=re.I)
    if remove:
        terms.append(remove.group(1).strip())

    return [term for term in terms if term]


def _sentence_has_active_evidence(db: StandardDatabase, sent_id: str) -> bool:
    affected = repo.affected_evidence(db, sent_id)
    return bool(affected["mentions"] or affected["relations"])


def related_sentences_for_instruction(
    db: StandardDatabase,
    doc_id: str,
    instruction: str,
    input_kind: str = "instruction",
) -> list[dict]:
    """Return existing evidence-bearing sentences in a document related to an edit.

    The offline selector is intentionally conservative: for commands like
    "remove Canada" or "replace Warsaw with Paris", it edits only sentences
    whose current text contains the old surface term and that already have
    provenance evidence. If no such command term exists, it falls back to
    evidence-bearing sentences whose current entity/relation endpoint names
    appear in the instruction.
    """
    sentences = repo.get_sentences(db, doc_id)
    focus_terms = (
        []
        if input_kind == "fact"
        else [normalize_name(term) for term in _instruction_focus_terms(instruction)]
    )
    normalized_instruction = normalize_name(instruction)

    selected = []
    for sentence in sentences:
        sent_id = sentence["_key"]
        if not _sentence_has_active_evidence(db, sent_id):
            continue

        normalized_text = normalize_name(sentence["text"])
        if focus_terms and any(term in normalized_text for term in focus_terms):
            selected.append(sentence)
            continue

        if focus_terms:
            continue

        triples = current_triples_for_sentence(db, sent_id)
        related_names = {
            normalize_name(name)
            for triple in triples
            for name in (triple[0], triple[2])
        }
        if any(name and name in normalized_instruction for name in related_names):
            selected.append(sentence)

    return selected


def semantically_related_sentences_for_instruction(
    db: StandardDatabase,
    doc_id: str,
    instruction: str,
    limit: int = 5,
    threshold: float | None = None,
) -> list[dict]:
    """Return evidence-bearing sentences selected by optional embeddings."""
    from ripplekg.extraction.embeddings import semantic_search

    rows = semantic_search(db, instruction, doc_id=doc_id, limit=limit, threshold=threshold)
    selected = []
    sentences = db.collection("sentences")
    for row in rows:
        sent_id = row["sent_id"]
        if not _sentence_has_active_evidence(db, sent_id):
            continue
        selected.append(sentences.get(sent_id))
    return selected


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
    *,
    superseded_triples: list[Triple] | None = None,
) -> list[Triple]:
    """Merge extractor candidates with old triples still supported by text.

    This is the deterministic guardrail that matches the project design:
    the LLM/IE provider proposes a new evidence set, then a semantic verifier
    checks whether old evidence remains supported by the edited sentence.

    The current implementation uses conservative surface-form entailment:
    if both old triple endpoints still appear in the edited sentence, keep the
    old triple even if the extractor omitted it. Relation names are kept from
    the original KG, so M1 can compare canonical triples reliably.

    For an explicit replacement edit, callers may identify the exact old
    triples being superseded. A replacement candidate with the same
    head/relation and a different tail then prevents only those old triples from
    being recovered. This avoids both stale evidence and accidental removal of
    unrelated values from multi-valued relations.
    """
    normalized_text = normalize_name(new_text)
    requested_superseded = set(superseded_triples or [])
    superseded_old_triples = {
        old
        for old in current_triples
        if old in requested_superseded
        and any(
            normalize_name(candidate_head) == normalize_name(old[0])
            and normalize_name(candidate_relation) == normalize_name(old[1])
            and normalize_name(candidate_tail) != normalize_name(old[2])
            for candidate_head, candidate_relation, candidate_tail in candidate_triples
        )
    }
    verified = [
        triple for triple in candidate_triples
        if triple not in superseded_old_triples
    ]
    seen = set(verified)

    for triple in current_triples:
        head, rel_type, tail = triple
        candidate_replaces_old = triple in superseded_old_triples
        still_supported = (
            normalize_name(head) in normalized_text
            and normalize_name(tail) in normalized_text
        )
        if still_supported and not candidate_replaces_old and triple not in seen:
            verified.append(triple)
            seen.add(triple)

    return verified


def _is_explicit_replacement(instruction: str) -> bool:
    return bool(re.search(r"\breplace\b.+\bwith\b", instruction, flags=re.I | re.S))


def _superseded_triples_for_instruction(
    current_triples: list[Triple],
    instruction: str,
) -> list[Triple]:
    """Return only old triples explicitly targeted by a replace instruction."""
    match = re.search(r"\breplace\s+(.+?)\s+with\s+(.+)$", instruction, flags=re.I | re.S)
    if not match:
        return []

    old_value = match.group(1).strip()
    old_value = re.sub(r"^the\s+old\s+fact\s+", "", old_value, flags=re.I)
    old_value = old_value.strip("'\"").rstrip(".").strip()
    old_norm = normalize_name(old_value)
    superseded = []
    for triple in current_triples:
        head, rel_type, tail = triple
        full_fact = normalize_name(f"{head} {rel_type} {tail}")
        if old_norm in {
            normalize_name(head),
            normalize_name(tail),
            full_fact,
        }:
            superseded.append(triple)
    return superseded


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
    input_kind: str = "instruction",
    skip_irrelevant: bool = False,
) -> EditOp | None:
    """Build an EditOp from a natural-language edit instruction or updated fact."""
    if provider == "heuristic" and input_kind == "fact":
        raise ValueError("input_kind='fact' requires provider='anthropic' or provider='openai'")

    sentence = get_sentence(db, doc_id, sent_idx)
    sent_id = sentence["_key"]
    current_triples = current_triples_for_sentence(db, sent_id)
    applies_to_sentence = True

    if provider == "heuristic":
        new_text = heuristic_rewrite(sentence["text"], instruction)
        intended = heuristic_intended_triples(current_triples, new_text)
    elif provider == "anthropic":
        from ripplekg.extraction.anthropic_provider import build_edit_with_anthropic

        new_text, intended, applies_to_sentence = build_edit_with_anthropic(
            sentence_text=sentence["text"],
            current_triples=current_triples,
            instruction=instruction,
            input_kind=input_kind,
        )
    elif provider == "openai":
        from ripplekg.extraction.openai_provider import build_edit_with_openai

        new_text, intended, applies_to_sentence = build_edit_with_openai(
            sentence_text=sentence["text"],
            current_triples=current_triples,
            instruction=instruction,
            input_kind=input_kind,
        )
    else:
        raise ValueError("provider must be 'heuristic', 'anthropic', or 'openai'")

    if not applies_to_sentence:
        if skip_irrelevant:
            return None
        new_text = sentence["text"]
        intended = current_triples

    intended = canonicalize_triples(intended, db=db)
    intended = verify_supported_old_triples(
        current_triples,
        new_text,
        intended,
        superseded_triples=(
            _superseded_triples_for_instruction(current_triples, instruction)
            if _is_explicit_replacement(instruction)
            else []
        ),
    )

    return EditOp(
        doc_id=doc_id,
        sent_idx=sent_idx,
        new_text=new_text,
        intended_triples=intended,
    )


def build_edits_for_document_instruction(
    db: StandardDatabase,
    doc_id: str,
    instruction: str,
    provider: str = "heuristic",
    input_kind: str = "instruction",
    selector: str = "evidence",
    semantic_limit: int = 5,
    semantic_threshold: float | None = None,
) -> list[EditOp]:
    """Build EditOps for all related existing sentences in one document."""
    if selector == "evidence":
        sentences = related_sentences_for_instruction(db, doc_id, instruction, input_kind)
    elif selector == "embedding":
        sentences = semantically_related_sentences_for_instruction(
            db,
            doc_id,
            instruction,
            limit=semantic_limit,
            threshold=semantic_threshold,
        )
    else:
        raise ValueError("selector must be 'evidence' or 'embedding'")

    edits = []
    for sentence in sentences:
        edit = build_edit_from_instruction(
            db,
            doc_id=doc_id,
            sent_idx=sentence["idx"],
            instruction=instruction,
            provider=provider,
            input_kind=input_kind,
            skip_irrelevant=input_kind == "fact",
        )
        if edit is not None:
            edits.append(edit)
    return edits
