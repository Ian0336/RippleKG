# RippleKG

Evidence-aware **incremental view maintenance (IVM)** for an LLM-generated knowledge graph,
built on **ArangoDB**. A corpus edit ripples along provenance → semantic evidence delta (M1)
→ cost-aware SKIP/PATCH/REBUILD decision (M2) → persisted freshness state — so the KG is
*surgically refreshed* instead of fully rebuilt.

Design docs (source of truth): [`docs/proposal.md`](docs/proposal.md) · [`docs/thought.md`](docs/thought.md) · [`docs/README.md`](docs/README.md)

## Architecture

Two services, managed by docker compose. **FastAPI imports `ripplekg` as a library — it _is_
the backend**; there is no second app process. All state lives in ArangoDB, so the API layer is
a thin, stateless wrapper. The same `pipeline.run_edit()` / `repo` functions back the scripts,
a notebook, and the web demo — three front doors, one logic.

```
docker compose
  arangodb:8529   ← single source of truth (KG + provenance + delta/decision logs + freshness)
  api:8000        FastAPI + import ripplekg + serves the static demo page
```

## Quickstart

```bash
cp .env.example .env          # adjust the password if you like
docker compose up -d          # arangodb + api
docker compose exec api python scripts/init_db.py   # create the 8 collections + indexes
open http://localhost:8000    # demo page (empty graph until ingest is implemented)
```

ArangoDB web UI: http://localhost:8529 (user `root`, password from `.env`).

## Layout (modules ↔ owner, see proposal §7)

```
src/ripplekg/
  models.py        shared contracts: EditOp / EvidenceDelta / Decision / EditResult / GraphView
  config.py        env-driven settings
  db/              ★B  client.py · schema.py (8 collections) · repo.py (the team seam)
  ingest/          ★A  DocRED → T0 graph
  edits/           ★A  synthetic edits (option A: intended_triples)
  mechanism/       ★C  pipeline.py (§10 entry, wired) · refresh.py (§11) — add delta.py (M1), policy.py (M2)
  baselines/       ★A/B2  B0/B1/B2 (optional, empty)
  eval/            ★D  metrics from logs (empty)
  service/         ★D  api.py + static/index.html (vis-network demo)
scripts/           init_db · ingest_t0 · run_edit
```

## Status

Working scaffold: infra + contracts + DB layer + FastAPI shell + the demo page run end-to-end
(`docker compose up`, `init_db`, `POST /edit` returns a valid `EditResult`). The
`ingest` / `edits` / `eval` / `baselines` packages and the M1/M2 parts of `mechanism` are
**empty placeholders** — each owner adds their own modules per the guide below and the
milestones in `docs/thought.md §16`.

## Implementing the stubbed modules

The folders below are empty packages on purpose — each owner writes their own modules. Suggested
shape (import the shared contracts from `ripplekg.models`, and do all DB access through
`ripplekg.db.repo` — never talk to ArangoDB directly from these modules):

**`ingest/`** — DocRED → T0 graph (owner A, milestone M2)
```python
# ingest/docred.py
def parse_docred(path: str) -> list[dict]: ...     # parse sents / vertexSet / labels(+evidence)
# ingest/loader.py
def ingest_document(db, doc: dict) -> None: ...     # write sentences + entities + relations
def ingest_dataset(db, path: str) -> int: ...       #   + provenance edges (mentions, sentence_supports_relation)
```

**`edits/`** — synthetic edits T1..Tn (owner A; option A = each edit carries intended_triples)
```python
# edits/store.py
from ripplekg.models import EditOp
def load_edits(path: str) -> list[EditOp]: ...
```

**`mechanism/`** — the core (owner C). `pipeline.run_edit` already exists and has a TODO block
showing exactly where these plug in (M1 → M2 → freshness → refresh):
```python
# delta.py  — M1, thought.md §10.4
from ripplekg.models import EvidenceDelta, Triple
def compute_delta(old_relations: list[dict], intended_triples: list[Triple]) -> list[EvidenceDelta]: ...
# policy.py — M2, thought.md §10.5
from ripplekg.models import Decision, EvidenceDelta
def decide(delta: EvidenceDelta, target_state: dict) -> Decision: ...   # SKIP / PATCH / REBUILD
# refresh.py — §11 (exists today as a no-op; fill in)
def apply_refreshes(db, step: int | None = None) -> dict: ...           # process pending decisions, flip freshness
```

**`eval/`** — metrics straight from the logs (owner D, thought.md §14)
```python
# eval/metrics.py
def summarize(db) -> dict: ...   # counts: added/removed/unchanged, SKIP/PATCH/REBUILD, cost vs full rebuild
```

**`baselines/`** — optional comparisons (owner A/B2, thought.md §13): `full_rebuild.py` (B0),
`generic_traversal.py` (B1), `naive.py` (B2).

## Dev notes

- `./src` is mounted into the `api` container with `uvicorn --reload` — edit code, no rebuild.
- Run tests: `pip install -e ".[dev]" && pytest` (DB-backed tests skip if ArangoDB is down).
