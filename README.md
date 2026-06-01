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
  extraction/      ★A/C edit generator: instruction → new_text + intended_triples
  mechanism/       ★C  pipeline.py · delta.py (M1) · policy.py (M2) · refresh.py (§11)
  baselines/       ★A/B2  naive.py (B2) · generic_traversal.py / aql_update.py (B1)
  eval/            ★D  metrics.py (log summaries)
  service/         ★D  api.py + static/index.html (vis-network demo)
scripts/           init_db · ingest_t0 · run_edit
```

## Status

Working end-to-end prototype: Re-DocRED ingest builds the T0 graph
(`scripts/ingest_t0.py` → entities/relations/provenance), synthetic edits load from
`data/edits/demo.json`, generated edits can be produced from an instruction via
`extraction/`, `pipeline.run_edit()` applies M1 evidence delta + M2 decisions,
`refresh.apply_refreshes()` handles immediate/deferred refresh, and metrics come from
persisted `evidence_deltas` / `refresh_decisions` logs.

## Implemented Modules

The main modules follow the ownership boundaries in the proposal. Shared contracts live in
`ripplekg.models`; common DB reads/writes go through `ripplekg.db.repo`.

**`ingest/`** and **`edits/`** — ✓ done (owner A): `ingest/docred.py` + `ingest/loader.py` build
the T0 graph from Re-DocRED; `edits/store.py` loads `data/edits/demo.json`.

**`extraction/`** — edit generator / extractor handoff (owner A/C): `editor.py` turns
`doc_id + sent_idx + instruction` into `EditOp(new_text, intended_triples)`. The default
`heuristic` provider runs offline for deterministic demos and tests; optional `anthropic`
and `openai` providers call an LLM when `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is configured.
All providers return the same contract consumed by M1/M2.

**`mechanism/`** — the core (owner C): `delta.py` computes added/removed/unchanged evidence
and updates provenance edges; `policy.py` chooses SKIP/PATCH/REBUILD; `pipeline.py` wires the
edit path; `refresh.py` processes pending decisions and flips objects back to fresh.

**`eval/`** — metrics straight from the logs (owner D, thought.md §14): `metrics.summarize()`
counts evidence deltas, decisions, nominal cost, and stale objects.

**`baselines/`** — optional comparisons (owner A/B2, thought.md §13): `full_rebuild.py` (B0),
`generic_traversal.py` (B1), `naive.py` (B2).

Current B2/AQL utilities:

```bash
# Inspect ArangoDB collection counts, stale objects, and optional sentence evidence.
python scripts/inspect_db.py --sent-id doc0:0 --show-indexes

# A/B/C end-to-end generated edit: instruction → EditOp → M1/M2.
docker compose exec api python scripts/run_generated_edit.py \
  --doc-id doc0 --sent-idx 4 --instruction "remove Canada" --provider heuristic

# Optional Claude provider; set ANTHROPIC_API_KEY and optionally ANTHROPIC_MODEL first.
docker compose exec api python scripts/run_generated_edit.py \
  --doc-id doc0 --sent-idx 4 --instruction "remove Canada" --provider anthropic

# Optional OpenAI provider; set OPENAI_API_KEY and optionally OPENAI_MODEL first.
docker compose exec api python scripts/run_generated_edit.py \
  --doc-id doc0 --sent-idx 4 --instruction "remove Canada" --provider openai
```

- `ripplekg.baselines.naive`: B2 naive invalidation. A changed sentence marks every
  mentioned entity/relation stale without semantic evidence delta.
- `ripplekg.baselines.generic_traversal`: generic AQL traversal baseline. A changed
  sentence invalidates every active KG object reachable through provenance edges.
- `ripplekg.baselines.aql_update`: graph-update baseline implemented as AQL traversal
  plus `UPDATE` statements inside ArangoDB.
- `scripts/inspect_db.py`: DB/debug helper for collection counts, stale objects,
  named graph status, indexes, affected evidence, and AQL explain plans.

Run the DB-backed tests inside Docker:

```bash
docker compose up -d
docker compose exec api pip install -e ".[dev]"
docker cp tests ripplekg-api-1:/tmp/ripplekg-tests
docker compose exec api pytest /tmp/ripplekg-tests/tests
```

## Dev notes

- `./src` is mounted into the `api` container with `uvicorn --reload` — edit code, no rebuild.
- Run tests: `pip install -e ".[dev]" && pytest` (DB-backed tests skip if ArangoDB is down).
