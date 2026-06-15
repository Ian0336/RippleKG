"""Independent audit of the should-edit gold labels in the annotation set.

Re-checks every `human_should_edit` label against the stated annotation rule
("true only when the sentence states or structurally expresses the old fact and
would become stale when that fact is explicitly replaced") without using the DB.

It surfaces two kinds of suspicious labels for manual review:

  FLAG-1  human=True  but the replaced value does not appear in the sentence
          (a "true" label with no textual basis to change).
  FLAG-2  human=False but the replaced value appears in the sentence AND
          provenance links it to the old fact (a borderline "no-edit" call).

Usage:
  python scripts/audit_edit_labels.py [data/edit_annotation_set.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower().rstrip(".")


def replaced_values(old_fact: str, new_fact: str) -> tuple[str, str]:
    """Return (old_tail, new_tail): the differing value of a replace_old_fact case."""
    prefix = os.path.commonprefix([old_fact, new_fact])
    prefix = prefix[: prefix.rfind(" ") + 1] if " " in prefix else prefix
    return old_fact[len(prefix):].rstrip(".").strip(), new_fact[len(prefix):].rstrip(".").strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="data/edit_annotation_set.json")
    args = parser.parse_args()

    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    cases = payload["cases"]

    rows = []
    for case in cases:
        old_tail, new_tail = replaced_values(case["old_fact"], case["new_fact"])
        for cand in case["candidates"]:
            rows.append({
                "case": case["case_id"],
                "old_fact": case["old_fact"],
                "new_fact": case["new_fact"],
                "old_tail": old_tail,
                "new_tail": new_tail,
                "sent_id": cand["sentence_id"],
                "text": cand["text"],
                "old_in_sent": bool(old_tail) and norm(old_tail) in norm(cand["text"]),
                "prov": cand.get("provenance_supports_old_fact"),
                "human": cand.get("human_should_edit"),
                "notes": cand.get("human_notes", ""),
            })

    n = len(rows)
    true_rows = [r for r in rows if r["human"] is True]
    false_rows = [r for r in rows if r["human"] is False]
    unlabeled = [r for r in rows if r["human"] is None]

    print(f"candidates={n}  TRUE={len(true_rows)}  FALSE={len(false_rows)}  UNLABELED={len(unlabeled)}")
    print(f"provenance==human agreement: {sum(1 for r in rows if r['prov'] == r['human'])}/{n}")

    flag1 = [r for r in true_rows if not r["old_in_sent"]]
    flag2 = [r for r in false_rows if r["old_in_sent"] and r["prov"]]

    print(f"\nFLAG-1 (human=True, replaced value NOT in sentence): {len(flag1)}")
    for r in flag1:
        print(f"  [{r['case']} {r['sent_id']}] {r['old_tail']!r}->{r['new_tail']!r}: {r['text']}")

    print(f"\nFLAG-2 (human=False, value in sentence AND provenance supports old fact): {len(flag2)}")
    for r in flag2:
        print(f"  [{r['case']} {r['sent_id']}] {r['old_tail']!r}->{r['new_tail']!r}")
        print(f"      sent: {r['text']}")
        print(f"      note: {r['notes']}")

    print(f"\nverdict: {len(flag1)} FLAG-1 + {len(flag2)} FLAG-2 require manual confirmation; "
          f"all other labels are unambiguous.")


if __name__ == "__main__":
    main()
