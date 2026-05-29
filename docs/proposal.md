# 面向 LLM 生成知識圖的增量檢視維護（IVM）

---

## 0. 專案 Scope

本專案聚焦於持久化的 `corpus -> KG` 維護路徑，而非完整 downstream GraphRAG flow。

必要路徑如下：

```text
corpus edit
  -> changed sentence/span
  -> affected evidence
  -> semantic evidence delta
  -> affected KG entity/edge
  -> SKIP/PATCH/REBUILD decision
  -> persisted KG/provenance/freshness state in ArangoDB
```

Community grouping、summary、embedding、QA 與 lazy query refresh 是 stretch goal。它們是有用的延伸，但不是核心貢獻的必要條件。

系統應改善三項性質：

- 相較 full corpus-to-KG rebuild，降低 update cost，
- 相較 naive chunk-to-entity 或 generic traversal invalidation，提高 invalidation 精確度，
- 為 evidence、delta、refresh decision 與 freshness metadata 提供持久化 DB 狀態。

---

## 1. Contribution

定位：

> RAG 把 semantic search 帶進資料庫；本專案把 incremental view maintenance 帶進 LLM 生成的知識圖。

Contribution 陳述：

> 本專案透過在 ArangoDB 內物化 evidence、semantic delta、invalidation decision 與 freshness metadata，為 LLM 生成的知識圖帶來 incremental view maintenance。

---

## 2. DBMS Contribution

### 2.1 將 Pipeline 對應到 DBMS 概念


| 專案元件                                | DBMS 概念                            |
| ----------------------------------- | ---------------------------------- |
| corpus update                       | base table update                  |
| document diff / changed spans       | capture change set                 |
| affected extraction windows         | dependency tracking (forward)      |
| mention/relationship evidence delta | view-level change detection        |
| affected entity / relationship      | derived-table propagation          |
| SKIP / PATCH / REBUILD decision     | cost-aware view maintenance policy |
| persisted freshness metadata        | materialized view freshness state  |


可選 downstream 列：


| Optional Flow                    | DBMS 概念                        |
| -------------------------------- | ------------------------------ |
| community impact decision        | aggregate invalidation policy  |
| summary / embedding invalidation | materialized aggregate refresh |


此 pipeline 是 LLM 時代的 incremental view maintenance：corpus update 改變 source record，再透過持久化 evidence 與 derived KG object 傳播。

### 2.2 為何經典 IVM 不足

經典 IVM 假設 view 是 deterministic、exact，且 refresh 相對便宜。LLM 生成的 view 違反這些假設：


| 經典 IVM 假設              | LLM 生成 View 的現實                              |
| ---------------------- | -------------------------------------------- |
| View 是 deterministic   | Re-extraction 可能對相同語意產生文字不同的 evidence        |
| Dependency 是 exact     | Dependency 是 fuzzy 且 semantic                |
| Refresh 足夠便宜可 eager 執行 | LLM extraction 或 judging 可能很貴，refresh 應選擇性執行 |


這 motivates 新的 maintenance 機制：semantic evidence delta、cost-aware invalidation 與持久化 freshness state。

### 2.3 新的資料庫能力


| 機制                  | 傳統 DB                   | 提案系統                                    |
| ------------------- | ----------------------- | --------------------------------------- |
| Dependency tracking | row/cell level, exact   | evidence level, semantic                |
| View refresh        | trigger-based refresh   | cost-aware SKIP/PATCH/REBUILD decision  |
| Output equivalence  | exact value comparison  | semantic equivalence                    |
| Freshness state     | binary 或 external state | persisted per-object freshness metadata |


---

## 3. 核心機制

提案圍繞三個 mechanism。M1 與 M2 是必要；M3 是 corpus-to-KG 路徑跑通後的 stretch goal。

### M1. Semantic Evidence Delta

給定同一受影響 window 的舊 evidence set 與新抽取 evidence set，M1 偵測哪些 evidence record 是 added、removed、modified，或儘管文字改變但 semantically unchanged。

為何需要：

- 經典 IVM 可用 exact value 或 hash 比較 row。
- LLM extraction 可能對同一 underlying claim 產生 paraphrase。
- 系統需要 semantic equivalence operator，基於 canonical-form matching、embedding similarity，以及對 ambiguous case 可選的 LLM judge。

評估：將 evidence delta 與 manual inspection 或 full re-extraction 的 oracle 比較，量測 precision 與 recall。

### M2. Cost-Aware KG Invalidation Policy

給定 evidence delta，M2 決定每個受影響 KG entity 或 relationship 應 `SKIP`、`PATCH` 還是 `REBUILD`。

Community、summary 與 embedding 是 entity/relationship maintenance 跑通後的 stretch target。

為何需要：

- 傳統 trigger 通常在 data 變 dirty 時 refresh。
- LLM 生成的 evidence 與 derived KG repair 可能很貴。
- Decision policy 應考慮 impact size、若可用的 query frequency、staleness tolerance 與 refresh cost。

評估：在固定 freshness target 下量測 update cost，或在固定 refresh budget 下量測 freshness。

### M3. Freshness Metadata，Query-Time Lazy Refresh 作為 Stretch

M3 的必要部分是：在 ArangoDB 持久化 per-object freshness metadata，使 demo 能展示哪些 KG entity 與 relationship 是 fresh、stale、skipped、patched 或 rebuilt。

Query-time `fresh_only=true`、`max_staleness=K`、lazy refresh、community summary 與 embedding refresh 應視為 stretch feature。

為何有用：

- 傳統 materialized view 要嘛 eager refresh，要嘛手動 refresh。
- Per-object freshness metadata 使 stale derived KG state 可見。
- 可選的 query-time lazy refresh 之後可使用此 metadata。

Stretch 版本的評估：query latency、stale answer rate 與 background queue length。

---

## 4. 系統 Stack

提案實作以 **ArangoDB only** 作為持久資料庫，contribution 框架為 LLM 生成 KG view 的 IVM layer。

### 4.1 為何 Single Database

- ArangoDB 可持久化 document、KG object、provenance edge、evidence delta、invalidation record 與 freshness metadata。
- Multi-database 設計會增加 cross-store consistency 與 integration 工作，且非 contribution 核心。
- 主要貢獻是 IVM mechanism，而非使用幾個資料庫。

### 4.2 持久化 DB 狀態

每個 stage 將 output 物化到 ArangoDB：

```text
corpus edits -> documents / extraction windows
extraction output -> evidence records
semantic delta -> evidence_deltas
invalidation decision -> refresh_decisions / invalidations
freshness state -> object_freshness / maintained_epoch fields
metrics -> experiment_metrics
```

Downstream stage 消費持久化記錄，而非 transient in-memory object。

---

## 5. Baselines


| ID     | 系統                                | Update 行為                          | 目的                           |
| ------ | --------------------------------- | ---------------------------------- | ---------------------------- |
| B0     | Full corpus-to-KG rebuild         | 從 edited corpus state 重建 KG        | 正確性參考                        |
| B1     | Generic AQL traversal on ArangoDB | 使每個 reachable object 失效            | 我們減少 over-invalidation       |
| B2     | Naive sentence/chunk invalidation | 直接標記 mentioned entity/edge 為 stale | 我們使用 semantic evidence delta |
| **B3** | **Ours (M1 + M2)**                | 持久化在 ArangoDB 的 evidence-level IVM | —                            |


可選 baselines：

- Vanilla RAG with BM25 and dense retrieval。
- Microsoft GraphRAG full rebuild。
- LightRAG-style graph-level incremental update。

ArangoDB traversal baseline 很重要，因為它展示 generic graph traversal 在沒有 evidence-aware invalidation 語意時能提供什麼。

---

## 6. 資料集、Demo 範圍與評估

**資料集決策**：使用 **DocRED 或 Re-DocRED**，而非 ATOM 2020-COVID-NYT。

理由：DocRED/Re-DocRED 提供 demo 所需的精確結構：

- `sents` = source corpus documents。
- `vertexSet` = entity node 與 mention。
- `labels` = relation edge。
- `labels.evidence` = 每條 KG edge 的 sentence-level provenance。

這不是預建好的 graph database dump，但是 deterministic 的 corpus-to-KG pair。我們可以直接從 annotated entity、relation 與 evidence link 建構初始 KG。

**更新模型**：

- `T0` = 原始 DocRED/Re-DocRED documents 與其 annotated KG edge。
- `T1..Tn` = 我們對選定 document 套用的 synthetic corpus edit。
- Demo 不需要 natural time-series update 或 post-update QA。

**Demo 目標**：

展示 corpus edit 正確改變 maintained KG：

```text
edit source sentence
  -> detect changed span
  -> find affected evidence
  -> compute evidence delta
  -> invalidate affected KG entity / edge
  -> skip, patch, or rebuild the KG object
  -> persist evidence delta, refresh decision, and freshness state in ArangoDB
  -> show before/after graph and provenance
```

**指標**：

- **Refresh cost**：每個 update step 的 LLM token 乘以 API price。
- **Graph delta correctness**：KG edge 的 add/remove/update 是否符合 synthetic edit 的預期效果。
- **Invalidation precision / recall**：選中 affected object 與 oracle full re-extraction diff 的比較。
- **Over-invalidation ratio**：提案 affected-set size 與 generic AQL traversal reachable-set size 的比較。
- **Freshness state correctness**：affected KG object 是否正確標記為 fresh、stale、skipped、patched 或 rebuilt。

**關鍵 demo 圖表**：X = synthetic edit step (`T0..Tn`)，Y = cost / invalidated object count / over-invalidation ratio / stale object count。

**預期結果**：full rebuild cost 最高，generic AQL traversal over-invalidates，提案系統只觸及 evidence 真正改變的 KG object。

---

## 7. 團隊分工

團隊分工應依 ownership 邊界，而非逐週時程。每個 area 有 primary owner，但 corpus 與 ArangoDB 基礎就緒後，實作應收斂到 mechanism 工作。


| 人      | Role                         | Owns                                                                                                                        |
| ------ | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **A**  | Baselines + Corpus Lead      | DocRED/Re-DocRED loader、synthetic edit scripts、full rebuild baseline、naive invalidation baseline、共用 extractor configuration |
| **B1** | ArangoDB + Schema Lead       | ArangoDB instance、collections、edge collections、indexes、provenance schema、持久化 object/freshness state                         |
| **B2** | ArangoDB + AQL Baseline Lead | ArangoDB data loading support、generic AQL traversal baseline、query/debug utilities、storage/index inspection                 |
| **C**  | Mechanism Lead (M1 + M2)     | M1 semantic evidence delta、M2 invalidation policy、cost model、proposed evidence-level IVM system                             |
| **D**  | Eval + Demo + Paper Lead     | Evaluation harness、core metrics、demo frontend/notebook support、slides、paper coordination、final video                        |


### Handoff Model

- **A 早期完成 corpus path，然後協助 C。** DocRED/Re-DocRED loader、synthetic edit scripts 與 baseline inputs 穩定後，A 應轉向 M1/M2 integration，並協助以 corpus oracle 驗證 evidence delta。
- **B1 與 B2 先學習並穩定 ArangoDB，然後協助 C。** 首要責任是讓 DB 基底可靠：schema、index、provenance edge、data loading 與 generic AQL traversal。穩定後，兩位 B 成員應協助 C 持久化 evidence delta、invalidation、refresh decision 與 freshness metadata。
- **C 擁有核心 mechanism。** C 應讓 `evidence_delta`、`refresh_decision` 與 `object_freshness` 的 interface 足夠清楚，使 A/B 能 plug in data 與 persistence code，而不改變 mechanism logic。
- **D 擁有 presentation surface 與 evaluation glue。** D 應提供 metric runner、figures、demo UI 或 notebook、demo 所需 frontend/backend glue、paper coordination、slides 與 video。D 不需要擁有核心 DB 或 M1/M2 logic。

## 8. 協調規則

- 保持單一共用 ArangoDB schema 作為 source of truth。
- 每個 pipeline stage 應將 output 寫入 ArangoDB，而非只回傳 Python object。
- A 與 C 應使用相同 extractor configuration，使實驗比較的是 maintenance strategy，而非不同 extraction 行為。
- B1/B2 應暴露簡單 helper API 供常用 DB 操作：load document、get affected evidence、write evidence delta、write refresh decision、query provenance。
- D 應從持久化 ArangoDB 記錄消費 demo 與 metrics，而非向各 component 索取 ad hoc in-memory state。

## 9. 實作重點

- **Topic**：面向 LLM 生成 KG view 的 incremental view maintenance。
- **Design**：M1/M2 加上持久化 ArangoDB state 是必要；M3 與 downstream GraphRAG artifact 是 stretch goal。
- **Implementation**：必要路徑是 `corpus edit -> evidence delta -> KG update -> persisted provenance/freshness state`。
- **Presentation**：demo 應展示一次 corpus edit 更新 KG edge 與 provenance，並附 cost、invalidation precision、over-invalidation 與 freshness 指標。

專案應圍繞讓 ArangoDB-backed corpus-to-KG path 跑通，再將可用人力投入 M1/M2 與 evaluation。