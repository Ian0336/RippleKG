"""Edit generators / extractors that produce EditOp before M1/M2.

The maintenance pipeline stays model-agnostic: this package turns a user edit
instruction into ``new_text`` + ``intended_triples``. The resulting EditOp is
then consumed by ``mechanism.pipeline``.
"""

from ripplekg.extraction.editor import build_edit_from_instruction
from ripplekg.extraction.editor import current_triples_for_sentence
from ripplekg.extraction.editor import heuristic_intended_triples

__all__ = [
    "build_edit_from_instruction",
    "current_triples_for_sentence",
    "heuristic_intended_triples",
]
