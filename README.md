# RippleKG

RippleKG is an evidence-aware incremental knowledge graph maintenance prototype
built on ArangoDB.

Instead of rebuilding an entire knowledge graph whenever a document changes,
RippleKG finds affected evidence sentences, generates sentence edits, computes
the evidence-level graph delta, and chooses whether each affected KG object
should be skipped, patched, or rebuilt.

```text
Updated fact or edit instruction
  -> Graph provenance / Transformer retrieval
  -> LLM relevance gate
  -> EditOp(new_text, intended_triples)
  -> M1 evidence delta: added / removed / unchanged
  -> M2 decision: SKIP / PATCH / REBUILD
  -> ArangoDB provenance and freshness update
```

## Current Results

The checked-in evaluation artifacts include a 50-fact retrieval benchmark and
an end-to-end benchmark with 30 fact-replacement cases and 150 reviewed
candidate sentences.

| Stage | Result |
|---|---:|
| Transformer retrieval Hit@1 | 86.0% |
| Transformer retrieval Hit@3 | 100.0% |
| LLM relevance-gate precision | 76.9% |
| LLM relevance-gate recall | 100.0% |
| Final EditOp content accuracy | 95.0% |
| M1 added/removed relation accuracy | 100.0% |
| End-to-end M2 decision accuracy | 100.0% |
| M2 policy-rule accuracy | 100.0% |

`Hit@K` measures whether at least one correct evidence sentence appears within
the first K search results. Therefore, `Hit@1 = 86%` means the first result was
correct for 43 of 50 facts, while `Hit@3 = 100%` means every fact had at least
one correct sentence somewhere in its first three results. It does not mean all
three results were correct.

These are small prototype results, not claims about general production
performance. The annotation set is assistant-reviewed and should be spot-checked
before use in a formal report.

## Terminology

The following names are important because several are RippleKG-specific rather
than general standards.

| Term | Meaning in this project |
|---|---|
| KG | Knowledge graph: entities connected by typed relations |
| Re-DocRED | The document-level relation-extraction dataset used to build the initial T0 graph and provenance |
| LLM | Large language model used here for relevance decisions, sentence rewriting, and triple extraction |
| fact / triple | One statement represented as `(head entity, relation type, tail entity)` |
| evidence sentence | A sentence connected to an entity or relation as supporting provenance |
| provenance | The stored link showing which sentence supports which KG object |
| evidence count | Number of currently active provenance edges supporting one KG object |
| one-hop traversal | Following one provenance edge from a changed sentence to its directly affected entities or relations |
| candidate sentence | A sentence retrieved for later relevance checking; it is not necessarily edited |
| T0 | The initial corpus, KG, and provenance state before incremental edits |
| incremental refresh | Updating only affected graph state instead of rebuilding the complete KG |
| `EditOp` | The normalized edit contract containing `doc_id`, `sent_idx`, `new_text`, and `intended_triples` |
| `intended_triples` | All triples the edited sentence should support after the edit, not the complete document KG |
| M1 | RippleKG's evidence-delta stage; this is a project stage name, not a general standard |
| M2 | RippleKG's refresh-decision stage; this is a project stage name, not a general standard |
| `SKIP` | Leave an unchanged KG object alone |
| `PATCH` | Incrementally update an affected object's evidence-derived state; it does not mean HTTP PATCH |
| `REBUILD` | Re-resolve one affected KG object after its last evidence disappears; it does not rebuild the whole KG |
| freshness | Whether an entity or relation is currently `fresh` or waiting for refresh as `stale` |
| relevance gate | A classifier that decides whether a candidate sentence truly needs editing |
| embedding | A numeric vector representing sentence text for similarity search |
| cosine similarity | A vector-similarity score used for ranking; higher means more similar, but it is not a correctness probability |
| schema merge | Mapping an LLM-generated relation label to the KG's canonical relation vocabulary |
| canonical relation | The normalized relation name used consistently by M1 comparisons |
| relation-aware verifier | Deterministic guardrail that validates triples and handles explicit replacements before M1 |
| synthetic replacement | A controlled, generated old-fact-to-new-fact update used for repeatable evaluation |
| AQL | ArangoDB Query Language |
| Gold Label | The expected correct answer used for evaluation |
| Top-K | The first K results returned by similarity ranking |
| similarity threshold | Minimum cosine-similarity score accepted by retrieval; it is not a probability |
| native vector index | ArangoDB's server-side indexed vector search |
| fallback scan | Exact Python cosine comparison used when the native vector index is unavailable |
| benchmark | A fixed evaluation workload used to measure the system |
| baseline | A simpler comparison method used to understand whether RippleKG improves on it |

## Main Ideas

### Evidence-aware incremental refresh

Relations and entities are connected to their source sentences through
provenance edges. When a sentence changes, RippleKG traverses only the affected
one-hop evidence instead of invalidating the entire graph.

### M1: evidence delta

M1 compares the sentence's old active evidence with the edited sentence's
`intended_triples`:

- `unchanged`: evidence still exists
- `added`: new evidence appeared
- `removed`: old evidence disappeared

### M2: refresh policy

M2 converts each M1 delta into a maintenance decision:

- `unchanged -> SKIP`
- `added -> PATCH`
- `removed` with other active evidence `-> PATCH`
- last active evidence removed `-> REBUILD`

`REBUILD` is intentionally local: it rebuilds or removes the affected entity or
relation only. RippleKG's purpose is to avoid rebuilding the complete graph.

### Hybrid sentence selection

RippleKG can combine:

- ArangoDB graph provenance for high-recall affected-sentence discovery
- Transformer embeddings for semantic retrieval
- an LLM relevance gate to remove unrelated candidates

Embedding search only selects candidates. M1 evidence delta and M2 policy remain
responsible for graph-update correctness.

## Technology

- Python 3.11+
- ArangoDB 3.12
- FastAPI
- Re-DocRED
- optional Sentence Transformers (`all-MiniLM-L6-v2`)
- optional Anthropic or OpenAI-compatible LLM provider
- Docker Compose

## ArangoDB Schema

RippleKG creates eight collections.

| Collection | Type | Purpose |
|---|---|---|
| `documents` | document | source-document metadata |
| `sentences` | document | sentence text, hashes, status, optional embeddings |
| `entities` | document | document-local KG entities and freshness |
| `relations` | document | KG relations and evidence counts |
| `mentions` | edge | sentence-to-entity provenance |
| `sentence_supports_relation` | edge | sentence-to-relation provenance |
| `evidence_deltas` | document | persisted M1 audit records |
| `refresh_decisions` | document | persisted M2 decisions |

The named graph is `ripplekg_graph`.

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
```

Do not commit `.env`; it is ignored by Git.

### 2. Download Re-DocRED

```bash
curl -fsSL -o data/docred/dev_revised.json \
  https://raw.githubusercontent.com/tonytan48/Re-DocRED/main/data/dev_revised.json
```

The downloaded dataset is ignored by Git. The file contains 500 documents.

### 3. Start services

```bash
docker compose up -d --build
```

Services:

- RippleKG web UI and API: http://localhost:8001
- FastAPI documentation: http://localhost:8001/docs
- ArangoDB web UI: http://localhost:8529

ArangoDB login uses `root` and the password configured in `.env`.

### 4. Initialize and ingest data

```bash
docker compose exec api python scripts/init_db.py
docker compose exec api python scripts/ingest_t0.py data/docred/dev_revised.json 5
```

The final argument is the document limit. Use `500` to ingest the complete dev
split:

```bash
docker compose exec api python scripts/ingest_t0.py \
  data/docred/dev_revised.json 500
```

`ingest_t0.py` truncates all eight project collections before loading T0. It
therefore also clears previous edits, embeddings, deltas, and decisions.

### 5. Run the scripted demonstration

```bash
docker compose exec api python scripts/showcase_results.py
```

This prints presentation-friendly SKIP and factual-change paths.

## LLM Configuration

Add one provider to `.env`:

```dotenv
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-20250514

OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
```

The project calls provider HTTP APIs directly and does not require their SDKs.

### Single-sentence fact update preview

```bash
docker compose exec api python scripts/run_generated_edit.py \
  --doc-id doc0 \
  --sent-idx 5 \
  --input-kind fact \
  --instruction "Schneider became the coach of the Russian skeleton team in July 2012." \
  --provider anthropic \
  --dry-run
```

### Document-level related-sentence update preview

```bash
docker compose exec api python scripts/run_generated_edit.py \
  --doc-id doc0 \
  --scope document \
  --selector embedding \
  --semantic-limit 3 \
  --semantic-threshold 0.30 \
  --input-kind fact \
  --instruction "Schneider became the coach of the Russian skeleton team in July 2012." \
  --provider anthropic \
  --dry-run
```

Remove `--dry-run` to transactionally write sentence changes, M1 deltas, M2
decisions, freshness state, and refreshed embeddings to ArangoDB.

### Offline deterministic edit

The heuristic provider supports controlled `remove` and `replace` instructions:

```bash
docker compose exec api python scripts/run_generated_edit.py \
  --doc-id doc0 \
  --scope document \
  --instruction "remove Schneider" \
  --provider heuristic \
  --dry-run
```

Fact input requires an LLM provider because a heuristic string replacement
cannot reliably rewrite a sentence from a new fact.

## Transformer Embeddings

Docker installs only core dependencies by default. Without
`sentence-transformers`, RippleKG uses a lightweight hashing-vector fallback.

Install CPU PyTorch first to avoid downloading CUDA packages:

```bash
docker compose exec api pip install torch \
  --index-url https://download.pytorch.org/whl/cpu
docker compose exec api pip install sentence-transformers
```

This installation lives inside the current container and disappears after the
container is rebuilt.

Compute Transformer embeddings:

```bash
docker compose exec api python scripts/compute_embeddings.py
```

Confirm that the output contains:

```text
backend=sentence-transformers:all-MiniLM-L6-v2
```

Search within one document:

```bash
docker compose exec api python scripts/semantic_search.py \
  --doc-id doc0 \
  --query "Schneider became the coach of the Russian skeleton team" \
  --limit 5 \
  --threshold 0.30
```

Omit `--doc-id` for global search.

### Native ArangoDB vector index

```bash
docker compose exec api python scripts/create_vector_index.py
```

ArangoDB 3.12 must be started with vector-index support for native indexed
search. If unavailable, RippleKG automatically falls back to an exact Python
cosine scan. Do not describe the fallback path as indexed vector search.

## Inspecting ArangoDB Results

Inspect collection counts, graph state, stale objects, indexes, and one
sentence's affected evidence:

```bash
docker compose exec api python scripts/inspect_db.py \
  --sent-id doc0:5 \
  --show-indexes
```

Useful API endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /graph` | current KG graph |
| `GET /sentences?doc_id=doc0` | document sentences |
| `GET /deltas` | persisted M1 deltas |
| `GET /decisions` | persisted M2 decisions |
| `GET /metrics` | summary metrics |
| `POST /edit` | apply one supplied `EditOp` |
| `POST /tick` | apply deferred refreshes |
| `POST /reset` | recreate the eight-collection schema |

## Evaluation

Checked-in result artifacts:

| File | Contents |
|---|---|
| `data/relation_retrieval_eval.json` | 50-fact embedding retrieval results |
| `data/edit_annotation_set.json` | 30 replacement cases and 150 reviewed candidates |
| `data/llm_relevance_gate_eval.json` | cached LLM gate decisions and metrics |
| `data/end_to_end_edit_eval.json` | cached EditOps and final M1/M2 evaluation |

### Embedding retrieval with Re-DocRED evidence as gold

```bash
docker compose exec api python scripts/evaluate_relation_retrieval.py \
  --cases 50 \
  --limit 10 \
  --scope document \
  --output data/relation_retrieval_eval.json
```

### Create and review should-edit annotations

```bash
docker compose exec api python scripts/create_edit_annotation_set.py \
  --cases 30 \
  --top-k 5 \
  --output data/edit_annotation_set.json
```

Review each `human_should_edit` field. The checked-in assisted-review labels can
be reapplied with:

```bash
python scripts/apply_reviewed_edit_labels.py data/edit_annotation_set.json
python scripts/evaluate_edit_annotations.py data/edit_annotation_set.json
```

### Evaluate the LLM relevance gate

This calls the selected LLM once per case. Results are cached and API failures
are retried:

```bash
docker compose exec api python scripts/evaluate_llm_relevance_gate.py \
  --provider anthropic \
  --output data/llm_relevance_gate_eval.json
```

Use `--refresh` only when intentionally discarding cached responses and paying
for new API calls.

### Evaluate final EditOps and M1/M2

This uses a separate `ripplekg_eval` database and does not modify the main
RippleKG database:

```bash
docker compose exec api python scripts/evaluate_end_to_end_edits.py \
  --provider anthropic \
  --output data/end_to_end_edit_eval.json
```

The script reports:

- edit-selection precision and recall
- EditOp generation failures and incorrect edits
- final EditOp content accuracy
- M1 added/removed relation accuracy
- end-to-end M2 accuracy
- M2 policy-rule accuracy

### Baseline benchmark

Compares the incremental M1/M2 path against the B0/B1/B2 baselines
(docs/thought.md §13) on many documents and reports:

- over-invalidation: objects we mark stale vs B1 generic-traversal and B2 naive
- cost: our nominal SKIP/PATCH/REBUILD cost vs a B0 full document rebuild
- B0 correctness: whether the maintained KG equals a from-scratch recomputation
  over active evidence (the IVM consistency invariant)

```bash
docker compose exec api python scripts/run_benchmark.py \
  --docs 50 \
  --edits 12 \
  --mode mixed \
  --json --rows-jsonl data/baseline_comparison_rows.jsonl \
  > data/baseline_comparison_eval.json
```

See `docs/experiment-results.md` §12 for the reported numbers.

## Project Files

### Core package

| Path | Purpose |
|---|---|
| `src/ripplekg/models.py` | shared `EditOp`, `EvidenceDelta`, `Decision`, `EditResult`, and graph contracts |
| `src/ripplekg/config.py` | environment-driven database settings |

### Database and ingest

| Path | Purpose |
|---|---|
| `src/ripplekg/db/client.py` | connects to ArangoDB and creates the configured database |
| `src/ripplekg/db/schema.py` | creates the eight collections, indexes, and named provenance graph |
| `src/ripplekg/db/repo.py` | shared data-access layer, AQL traversal, and persistence helpers |
| `src/ripplekg/ingest/docred.py` | parses and normalizes Re-DocRED JSON |
| `src/ripplekg/ingest/loader.py` | builds the T0 entities, relations, and provenance edges |
| `src/ripplekg/edits/store.py` | loads predefined synthetic `EditOp` JSON |

### Extraction and candidate selection

| Path | Purpose |
|---|---|
| `src/ripplekg/extraction/editor.py` | sentence selection, EditOp generation, replacement-aware triple verification |
| `src/ripplekg/extraction/embeddings.py` | Transformer/lightweight embeddings, storage, cosine search, optional vector index |
| `src/ripplekg/extraction/schema_merge.py` | maps LLM relation labels to the current KG relation schema |
| `src/ripplekg/extraction/anthropic_provider.py` | Anthropic sentence rewrite and triple extraction |
| `src/ripplekg/extraction/openai_provider.py` | OpenAI-compatible sentence rewrite and triple extraction |

### Incremental maintenance

| Path | Purpose |
|---|---|
| `src/ripplekg/mechanism/delta.py` | M1: computes and applies added/removed/unchanged evidence |
| `src/ripplekg/mechanism/policy.py` | M2: selects SKIP/PATCH/REBUILD |
| `src/ripplekg/mechanism/pipeline.py` | transactional orchestration and automatic embedding refresh |
| `src/ripplekg/mechanism/refresh.py` | applies immediate or deferred refresh decisions |

### Service, metrics, and baselines

| Path | Purpose |
|---|---|
| `src/ripplekg/service/api.py` | FastAPI endpoints over the same repo and pipeline functions |
| `src/ripplekg/service/static/index.html` | browser-based graph and edit demonstration |
| `src/ripplekg/eval/metrics.py` | summarizes persisted delta, decision, cost, and freshness logs |
| `src/ripplekg/eval/benchmark.py` | reusable multi-document benchmark helpers |
| `src/ripplekg/baselines/full_rebuild.py` | B0 full-rebuild correctness reference (recompute aggregates from active evidence) |
| `src/ripplekg/baselines/naive.py` | B2 naive invalidation baseline |
| `src/ripplekg/baselines/generic_traversal.py` | B1 generic provenance-traversal baseline |
| `src/ripplekg/baselines/aql_update.py` | B1 AQL traversal and update baseline |

### Command-line scripts

| Script | Purpose |
|---|---|
| `scripts/init_db.py` | initialize schema and indexes |
| `scripts/ingest_t0.py` | ingest Re-DocRED into a clean T0 graph |
| `scripts/run_demo.py` | compact scripted end-to-end demo |
| `scripts/showcase_results.py` | presentation-friendly SKIP and factual-change output |
| `scripts/run_edit.py` | apply a predefined synthetic edit |
| `scripts/run_generated_edit.py` | generate and optionally apply sentence/document edits |
| `scripts/inspect_db.py` | inspect database state and affected evidence |
| `scripts/compute_embeddings.py` | compute and store sentence embeddings |
| `scripts/semantic_search.py` | search stored sentence vectors |
| `scripts/create_vector_index.py` | create the optional native ArangoDB vector index |
| `scripts/match_relation_schema.py` | inspect relation-schema matching |
| `scripts/run_benchmark.py` | compare incremental maintenance with baselines |
| `scripts/evaluate_semantic_selection.py` | small hand-authored semantic retrieval diagnostic |
| `scripts/evaluate_relation_retrieval.py` | evaluate retrieval against Re-DocRED evidence |
| `scripts/create_edit_annotation_set.py` | generate should-edit review cases |
| `scripts/apply_reviewed_edit_labels.py` | reapply the checked-in assisted-review labels |
| `scripts/evaluate_edit_annotations.py` | evaluate retrieval against reviewed labels |
| `scripts/evaluate_llm_relevance_gate.py` | evaluate and cache LLM gate decisions |
| `scripts/evaluate_end_to_end_edits.py` | evaluate final EditOps and M1/M2 in an isolated database |

### Tests and documentation

| Path | Purpose |
|---|---|
| `tests/test_pipeline_m1_m2.py` | M1/M2 integration tests |
| `tests/test_extraction.py` | EditOp, schema merge, and triple-verifier tests |
| `tests/test_embeddings.py` | semantic-search and embedding-refresh tests |
| `tests/test_arangodb_features.py` | transactions, graph, indexes, and ArangoDB behavior |
| `tests/test_benchmark.py` | benchmark correctness |
| `docs/implementation.md` | implementation notes |
| `docs/experiment-results.md` | complete experiment setup, results, error analysis, and reproduction commands |
| `docs/proposal.md` | original project proposal |
| `docs/thought.md` | detailed design reasoning |

## Running Tests

Install development dependencies locally:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

DB-backed tests skip when ArangoDB is unavailable. To run tests against Docker
ArangoDB, keep `docker compose up -d` running and configure the local `.env`
with `ARANGO_URL=http://localhost:8529`.

Current focused verification:

```bash
pytest -q tests/test_extraction.py tests/test_pipeline_m1_m2.py tests/test_embeddings.py
```

## Known Limitations

- The current reviewed evaluation set is small and contains synthetic fact replacements.
- Re-DocRED evidence labels are useful gold proxies but are not always equivalent
  to sentences that truly require editing.
- LLM output can fail schema or formatting checks; evaluation scripts cache,
  retry, and report these failures.
- Transformer installation is not currently baked into the Docker image.
- Native ArangoDB vector indexing requires server-side vector-index support.
- The current verifier handles explicit replacements more reliably than arbitrary
  natural-language update intent.

## Before Pushing to Git

Check that secrets and generated caches are not accidentally staged:

```bash
git status --short
git check-ignore .env
```

`.env`, virtual environments, Python caches, and the raw 500-document Re-DocRED
download are ignored. The smaller evaluation JSON artifacts are intentionally
kept so other contributors can inspect and reproduce the reported results.
