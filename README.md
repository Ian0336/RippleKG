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

# T0 data: download the Re-DocRED dev split into data/docred/ (git-ignored)
curl -fsSL -o data/docred/dev_revised.json \
  https://raw.githubusercontent.com/tonytan48/Re-DocRED/main/data/dev_revised.json
# rel_info.json (P-id → readable name) for the demo docs is committed; the full
# 96-relation file ships with the DocRED Google Drive distribution.

docker compose exec api python scripts/init_db.py                                  # 8 collections + indexes
docker compose exec api python scripts/ingest_t0.py data/docred/dev_revised.json 5 # build T0 (5 docs)
open http://localhost:8001    # demo page — a real KG (pick doc0)
```

ArangoDB web UI: http://localhost:8529 (user `root`, password from `.env`).

### Re-run / reset the data

`scripts/ingest_t0.py` is idempotent — it truncates the 8 collections then reloads, so to
rebuild T0 just run it again (change the file / doc count as needed):

```bash
docker compose exec api python scripts/ingest_t0.py data/docred/dev_revised.json 5
```

This also clears `evidence_deltas` / `refresh_decisions` (back to a clean T0). To rebuild the
schema as well (e.g. after editing `schema.py`), reset first:

```bash
curl -X POST localhost:8001/reset
docker compose exec api python scripts/ingest_t0.py data/docred/dev_revised.json 5
```

## Layout (modules ↔ owner, see proposal §7)

```
src/ripplekg/
  models.py        shared contracts: EditOp / EvidenceDelta / Decision / EditResult / GraphView
  config.py        env-driven settings
  db/              ★B  client.py · schema.py (8 collections) · repo.py (the team seam)
  ingest/          ★A  docred.py (parse) · loader.py (build T0 graph)  ✓ done
  edits/           ★A  store.py (load synthetic edits, option A)        ✓ done
  mechanism/       ★C  pipeline.py (§10 entry, wired) · refresh.py (§11) — add delta.py (M1), policy.py (M2)
  baselines/       ★A/B2  B0/B1/B2 (optional, empty)
  eval/            ★D  metrics from logs (empty)
  service/         ★D  api.py + static/index.html (vis-network demo)
scripts/           init_db · ingest_t0 · run_edit
```

## Status

Working scaffold + **owner A's data foundation done**: Re-DocRED ingest builds the T0 graph
(`scripts/ingest_t0.py` → entities/relations/provenance), and the synthetic-edit loader
(`edits/store.py` + `data/edits/demo.json`) is in place. Still **placeholders** for their owners
(see guide below + `docs/thought.md §16`): M1/M2 in `mechanism/` (★C), `eval/` (★D),
`baselines/` (★A/B2).

## Implementing the stubbed modules

The folders below are empty packages on purpose — each owner writes their own modules. Suggested
shape (import the shared contracts from `ripplekg.models`, and do all DB access through
`ripplekg.db.repo` — never talk to ArangoDB directly from these modules):

**`ingest/`** and **`edits/`** — ✓ done (owner A): `ingest/docred.py` + `ingest/loader.py` build
the T0 graph from Re-DocRED; `edits/store.py` loads `data/edits/demo.json`.

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
