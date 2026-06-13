"""Edit generators / extractors that produce EditOp before M1/M2.

The maintenance pipeline stays model-agnostic: this package turns a user edit
instruction into ``new_text`` + ``intended_triples``. The resulting EditOp is
then consumed by ``mechanism.pipeline``.
"""

from ripplekg.extraction.editor import build_edit_from_instruction
from ripplekg.extraction.editor import build_edits_for_document_instruction
from ripplekg.extraction.editor import current_triples_for_sentence
from ripplekg.extraction.editor import heuristic_intended_triples
from ripplekg.extraction.editor import related_sentences_for_instruction
from ripplekg.extraction.editor import semantically_related_sentences_for_instruction

__all__ = [
    "build_edit_from_instruction",
    "build_edits_for_document_instruction",
    "current_triples_for_sentence",
    "heuristic_intended_triples",
    "related_sentences_for_instruction",
    "semantically_related_sentences_for_instruction",
]
