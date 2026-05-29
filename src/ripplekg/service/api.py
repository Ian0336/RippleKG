"""FastAPI backend. Imports ripplekg as a library — there is no second process.

Thin HTTP wrappers over the same pipeline/repo functions the scripts and
notebook call, so the demo can never drift from the real pipeline. All state
lives in ArangoDB; this layer is stateless apart from a per-process step counter.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from ripplekg.db import repo, schema
from ripplekg.db.client import get_db
from ripplekg.mechanism import pipeline, refresh
from ripplekg.models import EditOp, EditResult, GraphView

STATIC_DIR = Path(__file__).parent / "static"

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _state["db"] = get_db()
    _state["step"] = 0
    yield


app = FastAPI(title="RippleKG", lifespan=lifespan)


def _db():
    return _state["db"]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/graph", response_model=GraphView)
def get_graph(fresh_only: bool = False):
    return repo.fetch_graph(_db(), fresh_only=fresh_only)


@app.get("/sentences")
def get_sentences(doc_id: str | None = None):
    return repo.get_sentences(_db(), doc_id)


@app.post("/edit", response_model=EditResult)
def post_edit(edit: EditOp, refresh_mode: str = "deferred"):
    _state["step"] += 1
    return pipeline.run_edit(_db(), edit, _state["step"], refresh_mode)


@app.post("/tick")
def post_tick():
    return refresh.apply_refreshes(_db(), _state.get("step"))


@app.post("/reset")
def post_reset():
    schema.drop_schema(_db())
    schema.init_schema(_db())
    _state["step"] = 0
    return {"status": "ok", "message": "schema reset"}


@app.get("/deltas")
def get_deltas(step: int | None = None):
    return repo.list_deltas(_db(), step)


@app.get("/decisions")
def get_decisions(step: int | None = None):
    return repo.list_decisions(_db(), step)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
