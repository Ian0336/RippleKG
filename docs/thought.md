# 基於 ArangoDB 的 IVM：M1/M2 實作補充文件

## 0. 文件角色

本文件是 `proposal.md` 提案的實作補充文件。

它不是主提案。其目的是提供以下內容的詳細設計筆記：

- **M1：Semantic Evidence Delta**
- **M2：Cost-Aware KG Invalidation Policy**

官方專案 scope 是持久化的 `corpus -> KG` 維護路徑。本文件記錄實作 M1/M2 所需的 ArangoDB schema、provenance model、update path、evidence record、invalidation record 與可選 downstream 延伸。

## 1. 必要的 Corpus-to-KG 路徑

必要實作應聚焦於持久化的 `corpus -> KG` 維護路徑：

```text
corpus edit
  -> changed sentence/span
  -> affected extraction window
  -> old/new evidence
  -> semantic evidence delta
  -> affected entity/relationship
  -> SKIP/PATCH/REBUILD decision
  -> persisted KG/provenance/freshness state in ArangoDB
```

Community grouping、summarization、embedding、QA 與 query-time lazy refresh 是可選的 downstream GraphRAG flow。只有在 corpus-to-KG 路徑端到端跑通後才應實作。

此 scope 仍滿足資料庫要求：每個 pipeline stage 將 output 物化到 ArangoDB collection 或 edge collection，downstream stage 消費持久化記錄，而非 transient in-memory library object。

## 2. 實作動機

本實作補充文件研究如何僅使用 ArangoDB 作為持久資料庫，建構 M1/M2 pipeline。

必要的目標更新路徑如下：

```text
corpus update
  -> synthetic document edit
  -> changed sentences / spans
  -> affected extraction windows
  -> old/new mention evidence
  -> mention evidence delta
  -> relationship evidence delta
  -> affected KG entity / relationship
  -> SKIP / PATCH / REBUILD decision
  -> persisted KG/provenance/freshness state in ArangoDB
```

可選的 downstream GraphRAG 路徑：

```text
relationship delta
  -> local graph delta
  -> community impact decision
  -> summary and embedding invalidation
  -> freshness-aware retrieval
```

本專案不宣稱 ArangoDB 無法儲存這些資料。ArangoDB 可以儲存文件、圖依賴、metadata、invalidation 記錄與 maintenance task。研究問題反而是：

> 純 ArangoDB 系統能否在原生多模型資料庫之上，加入 GraphRAG 感知的 invalidation 語意、版本化 evidence 記錄與 freshness-aware 維護，同時相較於 generic graph traversal 與 full rebuild 避免不必要的重算？

在此版本中，所有持久狀態都存放在 ArangoDB：

```text
GraphRAG objects
provenance dependencies
evidence delta records
invalidation records
maintenance task records
epoch metadata
freshness metadata
```

沒有 RocksDB 元件。貢獻在邏輯資料庫層：schema 設計、dependency 建模、交易式更新協定、基於 AQL 的 affected-set 計算、版本化與 maintenance 排程。

### 2.1 資料集與 Demo Workload 決策

使用 **DocRED 或 Re-DocRED** 作為主要 demo 資料集。

原因是 DocRED/Re-DocRED 已包含精簡的 corpus-to-KG 對應：

```text
sents
  -> source corpus document

vertexSet
  -> entity node 與 mention

labels.head / labels.tail / labels.relation_id / labels.relation_text
  -> KG relationship edge

labels.evidence
  -> 每條 relationship edge 的 sentence-level provenance
```

這不是預建好的 graph database dump，但足以 deterministically 建構初始 `T0` 圖。系統應匯入原始 document text，從 `vertexSet` 建立 entity node，從 `labels` 建立 relationship edge，並將每條 edge 連到其 supporting evidence sentence。

Demo update workload 應為 synthetic，而非 natural time-series update：

```text
T0 = 原始 DocRED/Re-DocRED document + annotated KG
T1 = 手動編輯的 evidence sentence
T2 = 另一次 controlled sentence insertion/deletion/rewrite
...
Tn = 最終 edited corpus state
```

Demo 目的不是 question answering。目的是展示 maintenance 管線：

```text
edit corpus sentence
  -> detect changed span
  -> locate affected evidence
  -> re-extract or rematch evidence
  -> compute semantic evidence delta
  -> update KG edge, provenance, freshness, and maintenance records
```

Demo 至少應包含：一次改變 relation 的 edit、一次僅 paraphrase relation 的 edit，以及一次不應使無關 KG edge 失效的 non-evidence sentence edit。

## 3. 主要實作假設

主要假設是：

> 雖然 ArangoDB 提供通用文件與圖能力，但完全建構在 ArangoDB 之上的 evidence-aware 維護層，可以透過區分 reachability 與 semantic invalidation，減少 over-invalidation 與 recomputation。

關鍵區別如下：

```text
generic traversal:
  changed object -> all reachable derived objects

evidence-aware invalidation:
  changed spans -> affected extraction windows -> evidence deltas
  -> only objects whose maintained state may be stale
```

這使專案優於簡單的 ArangoDB demo。系統不只是儲存圖並遍歷，而是定義 GraphRAG 專屬 invalidation 語意，並將結果持久化為資料庫管理的狀態。

## 4. 為何純 ArangoDB 方案合理

當專案想優先達成以下目標時，純 ArangoDB 設計是合理的：

- 完整可運作的系統，
- 簡單的檢查與除錯，
- 單一持久資料庫，
- 較少的跨儲存一致性問題，
- 在課程時程內較易實作，
- 與 generic AQL traversal 的直接比較。

取捨是專案無法深度控制實體 storage layout、RocksDB key 格式或低層 write amplification。因此 DBMS 貢獻必須圍繞邏輯資料庫設計與系統評估來框架化：

- typed provenance schema，
- evidence-based 版本化記錄，
- 用於 affected-set 計算的 AQL 與 index 設計，
- 交易式 invalidation 記錄，
- durable maintenance task 集合，
- freshness-aware 查詢行為，
- 與 generic traversal 及 full rebuild 的比較。

## 5. 系統架構

架構如下：

```text
GraphRAG Maintenance Manager
  -> ArangoDB document collections
  -> ArangoDB edge collections
  -> ArangoDB indexes
  -> AQL queries and traversals
  -> ArangoDB transactions
  -> background workers using ArangoDB task collections
```

ArangoDB 負責：

- GraphRAG 物件儲存，
- provenance dependency 儲存，
- evidence delta 儲存，
- invalidation 儲存，
- task 儲存，
- epoch 儲存，
- 面向檢索的 freshness 欄位。

Maintenance manager 負責：

- synthetic document edit diff，
- changed sentence 與 span detection，
- extraction window 選取，
- evidence extraction，
- evidence delta 計算，
- semantic invalidation 規則，
- task 產生，
- worker 執行。

## 6. ArangoDB 集合

### 6.1 物件集合

GraphRAG 物件使用文件集合：

```text
documents
chunks
extraction_windows
mention_evidence
relationship_evidence
entities
relationships
communities
summaries
embeddings
```

### 6.2 維護集合

持久 maintenance 狀態使用文件集合：

```text
evidence_deltas
invalidations
maintenance_tasks
epochs
object_freshness
maintenance_metrics
```

### 6.3 邊集合

Provenance 與圖關係使用邊集合：

```text
document_has_chunk
chunk_has_window
window_has_mention
window_has_relationship_evidence
mention_resolves_to_entity
relationship_evidence_resolves_to_relationship
entity_in_community
relationship_in_community
community_has_summary
object_has_embedding
```

這些邊集合讓 ArangoDB 同時表示 GraphRAG 圖與 provenance 圖。

## 7. 版本化物件模型

### 7.1 通用版本欄位

每個版本化物件應包含：

```text
object_type
object_id
version
source_epoch
maintained_epoch
valid_from
valid_to
status
freshness_status
created_from_update_id
```

建議狀態：

```text
active
stale
refreshing
obsolete
deleted
```

### 7.2 Document 記錄

```text
document_id
document_version
content
content_hash
created_at
valid_from
valid_to
status
```

### 7.3 Chunk 記錄

```text
chunk_id
chunk_version
document_id
document_version
chunk_index
text
text_hash
span_start
span_end
valid_from
valid_to
status
```

### 7.4 Extraction Window 記錄

Extraction window 是 re-extraction 的單位。window 可以是 sentence、paragraph、chunk 或鄰近 chunk 群組。

```text
window_id
chunk_id
chunk_version
window_type
span_start
span_end
text_hash
context_hash
status
```

### 7.5 Mention Evidence 記錄

Canonical entity 不應直接從 chunk text 變更。Chunk 產生 mention evidence。

```text
mention_id
window_id
chunk_id
chunk_version
surface_text
normalized_text
span_start
span_end
context_hash
relation_context_hash
candidate_entity_ids
resolved_entity_id
confidence
extractor_version
valid_from
valid_to
status
```

### 7.6 Relationship Evidence 記錄

Relationship 從 relationship evidence 維護而來。

```text
relationship_evidence_id
window_id
chunk_id
chunk_version
source_mention_id
target_mention_id
resolved_source_entity_id
resolved_target_entity_id
relation_type
relation_text
confidence
context_hash
extractor_version
valid_from
valid_to
status
```

### 7.7 Canonical Entity 記錄

```text
entity_id
canonical_name
aliases
entity_type
evidence_count
active_mention_count
last_evidence_epoch
resolution_status
freshness_status
status
```

### 7.8 Relationship 記錄

```text
relationship_id
source_entity_id
target_entity_id
relation_type
evidence_count
weight
last_evidence_epoch
freshness_status
status
```

## 8. Provenance Schema

Provenance 圖應區分 source object、evidence object、aggregate object 與 derived object。

```text
Document -> Chunk
Chunk -> ExtractionWindow
ExtractionWindow -> MentionEvidence
ExtractionWindow -> RelationshipEvidence
MentionEvidence -> Entity
RelationshipEvidence -> Relationship
Entity -> Community
Relationship -> Community
Community -> Summary
Object -> Embedding
```

此精煉 schema 避免 naive 假設：

```text
Chunk -> Entity
```

系統改為使用：

```text
Chunk -> ExtractionWindow -> MentionEvidence -> Entity
```

這很重要，因為 chunk 更新可能只影響 chunk 的一部分、可能保留 mention 表面文字但改變 context，或可能 catastrophically 重寫舊 chunk 版本的所有 evidence。

## 9. ArangoDB 索引設計

### 9.1 物件查詢索引

在以下欄位建立 persistent index：

```text
document_id
document_version
chunk_id
chunk_version
window_id
mention_id
relationship_evidence_id
entity_id
relationship_id
community_id
```

### 9.2 版本與 Freshness 索引

在以下欄位建立索引：

```text
status
freshness_status
source_epoch
maintained_epoch
valid_from
valid_to
```

這支援如下查詢：

```text
find stale objects
find active object versions
find objects maintained before a given epoch
```

### 9.3 Task 索引

在以下欄位建立索引：

```text
task_type
status
priority
source_epoch
leased_until
owner_type
owner_id
dedup_key
```

這支援 durable 背景 worker。

### 9.4 Delta 與 Invalidation 索引

在以下欄位建立索引：

```text
update_id
epoch
delta_type
object_type
object_id
source_document_id
source_chunk_id
status
```

這支援稽核、replay 與評估。

## 10. 更新路徑

### 10.1 輸入

```text
document_id
old_document_version
new_document_version
new_content
update_id
epoch
```

### 10.2 步驟 1：寫入新 Document 版本

在 ArangoDB transaction 中：

```text
insert new document version
mark previous document version obsolete or valid_to = epoch
advance committed_epoch
```

### 10.3 步驟 2：計算 Chunk Diff

Maintenance manager 計算：

```text
unchanged chunks
added chunks
removed chunks
modified chunks
changed spans
```

對原型而言：

- 以 paragraph 或 fixed-size token window 切分文件，
- 計算 `text_hash`，
- 用 text similarity 區分 small edit 與 catastrophic rewrite。

### 10.4 步驟 3：選取受影響的 Extraction Window

對 small edit：

```text
changed span
  -> sentence window
  -> paragraph window
  -> optional previous/next chunk window
```

對 catastrophic rewrite：

```text
old chunk version
  -> retract all old windows and evidence

new chunk version
  -> create all new windows and evidence
```

### 10.5 步驟 4：計算 Evidence Delta

對每個受影響的 extraction window：

```text
load old mention evidence
extract new mention evidence
match old and new mentions
compute added, removed, context_changed mentions

load old relationship evidence
extract new relationship evidence
match old and new relationship evidence
compute added, removed, changed relationship evidence
```

將記錄持久化到 `evidence_deltas`。

範例 delta 記錄：

```text
delta_id
update_id
epoch
delta_type
object_type
old_object_id
new_object_id
source_document_id
source_chunk_id
window_id
reason
status
```

### 10.6 步驟 5：計算 Semantic Invalidation Frontier

Semantic invalidation frontier 是套用 evidence delta 規則後，maintained state 可能 stale 的物件集合。

規則如下：

```text
removed mention evidence:
  decrement entity evidence
  maybe mark entity stale
  maybe mark entity embedding stale

added mention evidence:
  resolve entity
  maybe create entity candidate
  maybe mark entity embedding stale

mention context changed:
  re-evaluate entity resolution
  re-evaluate local relationship evidence

removed relationship evidence:
  decrement relationship evidence
  maybe mark relationship stale
  maybe mark local communities affected

added relationship evidence:
  update relationship aggregate
  maybe mark local communities affected

significant relationship weight delta:
  mark touched communities stale
  mark community summaries and embeddings stale
```

將失效物件持久化到 `invalidations`。

### 10.7 步驟 6：產生 Maintenance Task

對每個失效物件，建立 durable task 記錄：

```text
refresh_entity_resolution
repair_relationship_aggregate
repair_local_community
refresh_summary
refresh_embedding
garbage_collect_old_versions
```

Task 記錄：

```text
task_id
task_type
owner_type
owner_id
source_epoch
priority
status
dedup_key
leased_by
leased_until
retry_count
last_error
```

### 10.8 步驟 7：更新 Freshness 欄位

更新受影響的 ArangoDB 物件：

```text
freshness_status = stale
source_epoch = current_epoch
maintained_epoch = previous_maintained_epoch
```

這將 staleness 暴露給檢索查詢。

## 11. 背景 Maintenance Worker

Worker 輪詢 `maintenance_tasks`。

### 11.1 Claim Task

```text
find pending task ordered by priority and source_epoch
set status = running
set leased_by = worker_id
set leased_until = now + lease_duration
```

### 11.2 Execute Task

Task 範例：

```text
refresh_entity_resolution:
  resolve affected mention evidence to canonical entities
  update entity evidence counts

repair_relationship_aggregate:
  recompute relationship evidence count and weight

repair_local_community:
  mark touched communities fresh or update simple local labels

refresh_summary:
  regenerate or simulate summary refresh

refresh_embedding:
  regenerate or simulate embedding refresh
```

### 11.3 Complete Task

```text
update owner object maintained_epoch
update owner object freshness_status = fresh
mark invalidation resolved
mark task done
advance maintained_epoch if possible
```

### 11.4 Recovery

重啟時：

```text
find running tasks with leased_until < now
mark them pending
retry until retry_count limit
```

這讓純 ArangoDB 系統在沒有外部 queue 的情況下，仍具 durable task 行為。

## 12. 查詢語意

檢索查詢應暴露 freshness。

查詢可要求：

```text
fresh_only = true
```

或：

```text
allow_stale = true
max_staleness_epochs = 5
```

範例行為：

- 若 graph structure 是 fresh 但 summary 是 stale，回傳 community 並附 stale-summary 旗標。
- 若 embedding 是 stale，`fresh_only` 模式下可跳過，或回傳並附 freshness metadata。
- 若 recent chunk 尚未 embedded，fallback 到 direct chunk search。

這使 maintenance 狀態可見且可量測。

## 13. 基準

### 13.1 Full Rebuild

每次 synthetic document edit 後：

```text
rebuild chunks
re-extract all mention evidence
rebuild all relationship evidence
recompute all entity aggregates
recompute all relationship aggregates
recompute communities
refresh summaries and embeddings
```

這是正確性參考。

### 13.2 Generic ArangoDB AQL Traversal

使用一般圖遍歷：

```text
changed document or changed sentence
  -> all reachable chunks
  -> all reachable windows
  -> all reachable mentions
  -> all reachable entities
  -> all reachable relationships
  -> all reachable communities
```

此基準展示沒有 GraphRAG-aware invalidation 語意時 ArangoDB 能提供什麼。

### 13.3 Naive Chunk-to-Entity Invalidation

直接從 changed chunk 或 changed sentence 使物件失效：

```text
changed chunk or changed sentence
  -> all mentioned entities stale
  -> all adjacent relationships stale
  -> all related communities stale
```

此基準展示跳過 evidence-delta 層的成本。

### 13.4 提案：純 ArangoDB Evidence-Aware Maintenance

使用：

```text
changed sentences / spans
  -> affected extraction windows
  -> mention evidence deltas
  -> relationship evidence deltas
  -> semantic invalidation frontier
  -> durable ArangoDB maintenance tasks
```

## 14. 評估指標

### 14.1 Invalidation 品質

- Generic traversal 的可達物件數。
- 候選受影響物件數。
- 實際失效物件數。
- 相對每次 synthetic edit 預期效果的 graph delta correctness。
- Over-invalidation ratio。
- 與 full rebuild 結果的差異。

### 14.2 更新效能

- Document update 延遲。
- Affected window 計算時間。
- Evidence delta 計算時間。
- Invalidation frontier 計算時間。
- ArangoDB read/write 數。
- AQL 查詢數。
- Transaction 時間。

### 14.3 維護成本

- Re-extracted window 數。
- 新增、移除或變更的 mention evidence 記錄數。
- 新增、移除或變更的 relationship evidence 記錄數。
- 修復的 entity 數。
- 修復的 relationship 數。
- 受影響的 community 數。
- 刷新的 summary 與 embedding 數。

### 14.4 儲存成本

- 物件集合大小。
- 邊集合大小。
- Evidence delta 集合大小。
- Invalidation 集合大小。
- Maintenance task 集合大小。
- 相對於 full rebuild 基準的 storage overhead。

### 14.5 Freshness

- Stale 物件數。
- 從 invalidation 到 refresh 的時間。
- Task queue 長度。
- `committed_epoch - maintained_epoch`。
- 觸及 stale 物件的 query rate。

## 15. 最小可行範圍

### 15.1 必須實作

- DocRED/Re-DocRED loader。
- Synthetic edit script loader。
- ArangoDB 集合與邊集合。
- 版本化 documents 與 chunks。
- Extraction windows。
- Mention evidence 記錄。
- Relationship evidence 記錄。
- Entity 與 relationship aggregate 記錄。
- Evidence delta 記錄。
- Invalidation 記錄。
- Maintenance task 記錄。
- Epoch metadata。
- Generic AQL traversal 基準。
- Naive chunk-to-entity 基準。
- Full rebuild 基準。

### 15.2 可簡化

- `T0` 的 entity extraction 可使用 DocRED/Re-DocRED `vertexSet` annotation。
- `T0` 可使用 DocRED/Re-DocRED `labels`；edited window 可使用 simple deterministic 或 LLM-based extraction。
- Entity resolution 可使用 normalized name。
- Community repair 可只標記 local community stale，而非執行完整 incremental community detection。
- Summary 與 embedding refresh 可用固定成本模擬。

### 15.3 第一版不應實作

- Production-quality NER。
- Production-quality entity resolution。
- 完整 QA 評估。
- 完整 LLM summarization。
- 完整 vector index maintenance。
- ArangoDB 原始碼修改。
- RocksDB 或其他外部 storage 子系統。

## 16. 實作里程碑

### Milestone 1：ArangoDB Schema

- 定義物件集合。
- 定義邊集合。
- 為 ID、version、status、freshness 與 task 建立索引。

### Milestone 2：初始 GraphRAG Ingestion

- 載入 DocRED/Re-DocRED documents。
- 切分為 chunks。
- 建立 extraction windows。
- 從 `vertexSet` 建立 mention evidence。
- 從 `labels` 與 `labels.evidence` 建立 relationship evidence。
- 建構 entities、relationships 與 communities。
- 儲存 provenance 邊。

### Milestone 3：Synthetic Document Update 與 Evidence Delta

- 實作 synthetic edit application。
- 實作 document diff。
- 偵測 changed sentence 與 span。
- 選取受影響的 extraction windows。
- 計算新舊 mention evidence delta。
- 計算新舊 relationship evidence delta。
- 儲存 `evidence_deltas`。

### Milestone 4：Semantic Invalidation Frontier

- 實作 invalidation propagation rules。
- 儲存 `invalidations`。
- 更新物件 freshness 欄位。
- 產生 maintenance task。

### Milestone 5：Background Worker

- 實作 task claiming。
- 實作 task completion。
- 模擬或執行 repair。
- 追蹤 maintained epoch。

### Milestone 6：基準與實驗

- 實作 full rebuild。
- 實作 generic AQL traversal。
- 實作 naive chunk-to-entity invalidation。
- 執行 synthetic edit workload。
- 比較 invalidation 規模、recomputation 成本、update 延遲、storage overhead 與 freshness。

## 17. 風險與緩解

### 風險：專案看起來太像應用層

緩解：

- 將所有 maintenance 狀態持久化在 ArangoDB 集合。
- 對 version、invalidation 與 task 寫入使用 ArangoDB transaction。
- 與 generic AQL traversal 比較。
- 量測 index 與 storage overhead。
- 將貢獻框架化為 evidence-aware incremental view maintenance 的邏輯資料庫支援。

### 風險：Evidence Extraction 成為主要工作

緩解：

- 使用 deterministic extraction。
- 聚焦 delta、invalidation 與 maintenance 語意。

### 風險：Community Repair 過於複雜

緩解：

- 先追蹤 touched community。
- 模擬 local repair 成本。
- 評估 touched community 減少量，而非 perfect clustering 品質。

### 風險：Task Scheduling 耗時過多

緩解：

- 實作簡單的 lease-based worker 模型。
- 只在基本層級支援 retry 與 deduplication。

## 18. 最終框架

建議標題：

> **Evidence-Aware Incremental GraphRAG Maintenance on ArangoDB**

替代標題：

- **GraphRAG-Aware Invalidation Semantics on a Native Multi-Model Database**
- **Versioned Evidence Maintenance for Incremental GraphRAG in ArangoDB**
- **ArangoDB-Based Provenance and Freshness Tracking for Incremental GraphRAG**

建議的一句話貢獻：

> 本專案建構純 ArangoDB 的增量 GraphRAG 維護系統，持久化版本化 evidence 記錄、evidence delta、invalidation 記錄、maintenance task 與 freshness metadata，使 semantic invalidation frontier 計算能相較於 generic AQL traversal 與 naive chunk-level invalidation 減少不必要的 recomputation。

## 19. 結論

純 ArangoDB 版本是最實務的實作路徑。

若專案框架為：

```text
not:
  use ArangoDB to store and traverse a GraphRAG graph

but:
  use ArangoDB as a native multi-model substrate and add
  evidence-aware invalidation, versioned derived objects,
  durable maintenance tasks, and freshness-aware query semantics
```

此版本比 RocksDB 子系統更不偏向 storage engine，但若評估聚焦於邏輯資料庫設計、持久 maintenance 狀態、交易式 invalidation 記錄與可量測的 recomputation 減少，仍可成為有效的 DBMS 面向專案。
