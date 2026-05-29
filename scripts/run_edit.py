"""Apply one synthetic edit through the pipeline and print the EditResult.

Same entry point (pipeline.run_edit) the API and notebook use.
"""
from ripplekg.db.client import get_db
from ripplekg.mechanism.pipeline import run_edit
from ripplekg.models import EditOp

if __name__ == "__main__":
    db = get_db()
    edit = EditOp(
        doc_id="demo",
        sent_idx=0,
        new_text="(edited sentence text)",
        intended_triples=[],
    )
    result = run_edit(db, edit, step=1)
    print(result.model_dump_json(indent=2))
