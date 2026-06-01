"""Apply one synthetic edit through the pipeline and print the EditResult.

Same entry point (pipeline.run_edit) the API and notebook use.
"""
import argparse

from ripplekg.db.client import get_db
from ripplekg.edits.store import load_edits
from ripplekg.mechanism.pipeline import run_edit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edits", default="data/edits/demo.json")
    parser.add_argument("--index", type=int, default=0, help="0-based edit index")
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument(
        "--refresh-mode",
        choices=["immediate", "deferred"],
        default="immediate",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    db = get_db()
    edits = load_edits(args.edits)
    edit = edits[args.index]
    result = run_edit(db, edit, step=args.step, refresh_mode=args.refresh_mode)
    print(result.model_dump_json(indent=2))
