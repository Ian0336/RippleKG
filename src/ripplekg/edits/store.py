"""Load synthetic edits (T1..Tn) from disk. Owner: A.

File format (JSON list); option A = each edit carries its intended triples:
  [{"doc_id": "doc0", "sent_idx": 3, "new_text": "...",
    "intended_triples": [["Head", "rel_name", "Tail"]]}, ...]
"""
import json

from ripplekg.models import EditOp


def load_edits(path: str) -> list[EditOp]:
    with open(path, encoding="utf-8") as f:
        return [EditOp(**e) for e in json.load(f)]
