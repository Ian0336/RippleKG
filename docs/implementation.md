# 基於 IVM 的 GraphRAG 實作工作

本文件整理完成本專案所需的實作工作。刻意排除人力配置、責任分工與時程規劃。目標是定義系統必須建構、執行、量測與交付的內容。

## 1. 專案目標

為 LLM 生成的知識圖建構增量維護層，使 corpus 更新能在不進行完整重建的情況下，傳播到持久化的 KG。

系統應改善三項性質：

- 相較完整 GraphRAG 重建，降低 refresh 成本。
- 相較不持久化 evidence 層級 update state 的 graph-only incremental 系統，提升 freshness。
- 相較 naive chunk 層級或 graph traversal invalidation，提高 invalidation 精確度。

核心系統是以 ArangoDB 為後端的 corpus-to-KG 管線，具備 evidence 層級 dependency 追蹤、semantic delta 偵測、cost-aware invalidation 與持久化的 freshness metadata。Community grouping、summary、embedding 與 query-time lazy refresh 是 corpus-to-KG 路徑端到端跑通後的可選延伸。

## 2. 端到端系統流程

實作管線應支援以下流程：

1. 載入由 DocRED/Re-DocRED 加上 synthetic edit step 建構的版本化 corpus。
2. 在 `T0` 建構初始 corpus-to-KG 狀態。
3. 套用來自 `T1..Tn` 的 synthetic corpus 更新。
4. 偵測變更的 sentence、span 或受影響的 extraction window。
5. 僅對受影響 window 重新抽取 evidence。
6. 以 semantic equivalence 比較新舊 evidence。
7. 將 evidence delta 傳播到受影響的 entity 與 relationship。
8. 決定每個受影響 KG object 應 skip、patch 或 rebuild。
9. 將所有 evidence、delta、invalidation、refresh-decision 與 freshness 記錄持久化在 ArangoDB。
10. 記錄 graph delta 正確性、invalidation precision、refresh cost 與 freshness 指標。

可選 stretch flow：

- 將 relationship delta 傳播到 community。
- 使 summary 與 embedding 失效或 refresh。
- 以可選 lazy refresh 提供 freshness-aware retrieval query。

## 3. Corpus 與更新時間軸

實作 DocRED 或 Re-DocRED 的可重現 corpus loader。資料集作為初始 corpus-to-KG pair：documents 提供 corpus，entity annotation 提供 KG node，relation label 提供 KG edge，evidence sentence ID 提供 provenance。

必要工作：

- 下載並正規化 DocRED 或 Re-DocRED。
- 保留 document title、sentence token、entity mention、entity type、relation label、relation ID 與 evidence sentence ID。
- 將每份 document 轉換為 deterministic source document ID。
- 從原始 dataset document 與 annotated KG edge 建構 `T0`。
- 透過對選定 evidence sentence 套用 controlled edit，產生 synthetic update step `T1..Tn`。
- 將每個 corpus edit 儲存為 structured metadata，使每次 demo 都能確定性重現。
- 保留原始 annotation 作為初始 KG，以及判定每次 synthetic edit 應影響哪些 graph object 的參考。

Demo workload 至少應包含以下 edit 類型：

- 新增表達新 relation 的 sentence。
- 刪除或削弱支撐既有 relation 的 sentence。
- 以等價語意改寫 supporting sentence。
- 改寫 supporting sentence 使 relation 語意改變。
- 編輯 non-evidence sentence，以展示無關 graph edge 不應被 invalidation。

## 4. Extraction 層

實作提案系統與 baselines 共用的 extraction 層。

必要工作：

- 將 DocRED/Re-DocRED entity 與 relation annotation 載入為 `T0` 的 seed evidence。
- 在 synthetic corpus edit 後，從變更的 text window 抽取 atomic evidence。
- 將每個 evidence item 正規化為穩定的結構化表示。
- 從 evidence 抽取或推導 entity mention 與 relationship tuple。
- 使用 LLM extraction 時，跨系統保持 extraction prompt、model 設定與 parser 行為一致。
- 追蹤 token 用量、model latency、extraction 失敗與 retry 次數。
- 透過穩定 prompt、temperature 設定、canonicalization 與 cached output，盡可能使 extraction 具確定性。

Evidence 記錄至少應包含：

- Evidence ID。
- Source document ID。
- Corpus version 或 update step。
- Text span 或 extraction window。
- Canonical claim text。
- Mentioned entities。
- Relationship tuple（若可用）。
- Embedding 或 embedding reference。
- Extraction metadata。

## 5. ArangoDB 儲存模型

將專案實作為單一資料庫的 ArangoDB 系統。

必要集合：

- Documents。
- Text chunks 或 extraction windows。
- Evidence items。
- Entities。
- Mentions。
- Relationships。
- Refresh decisions。
- Invalidations。
- Object freshness records。
- Query logs。
- Experiment metrics。

可選集合：

- Communities。
- Summaries。
- Embeddings 或 embedding metadata。

必要邊集合：

- Document-to-window provenance。
- Window-to-evidence provenance。
- Evidence-to-entity links。
- Evidence-to-relationship links。
- Relationship graph edges。
- Derived-object dependency links。

可選邊集合：

- Entity-to-community links。
- Summary-to-source evidence links。

必要索引：

- Source document 與 corpus version 索引。
- Evidence canonical key 索引。
- Entity 與 relationship lookup 索引。
- Freshness 與 stale-state 索引。
- Refresh-decision 與 invalidation 索引。
- 若包含 query log，則需供 cost-aware invalidation 使用的 query-frequency 索引。
- 若 embedding 作為延伸實作，則需 vector index。

## 6. 初始 GraphRAG 建構

實作 `T0` 的完整初始建構。

必要工作：

- 匯入 `T0` DocRED/Re-DocRED documents。
- 將文件切分為 chunk 或 window 以供 extraction。
- 從 annotated relation label 與 evidence sentence ID 建立 seed evidence 記錄。
- 從 `vertexSet` annotation 解析 entities。
- 從 relation label 建立 relationship edges。
- 儲存後續增量 KG maintenance 所需的所有 provenance link。
- 將 evidence、entity、relationship、provenance、freshness 與 experiment metadata 持久化在 ArangoDB。

KG 路徑穩定後的可選工作：

- 建構 communities。
- 產生 community summaries。
- 依需要為 document、chunk、entity、community 與 summary 產生 embedding。

此完整建構也是 full-rebuild baselines 與 oracle 比較所需的參考行為。

## 7. Change Detection

實作 controlled synthetic corpus edit 的 update ingestion。

必要工作：

- 將每個 edited corpus version 與前一版本比較。
- 識別 inserted、deleted 與 modified sentences。
- 識別 changed span 與受影響的 extraction window。
- 標記受影響 window 以供 re-extraction。
- 在 delta 比較完成前，保留舊 evidence 與衍生物件。
- 記錄 edit script 與 update metadata 以支援可重現性。

此階段輸出應為可驅動 evidence 層級 maintenance 的 change set。

## 8. M1：Semantic Evidence Delta

實作舊 evidence set 與新抽取 evidence set 之間的 semantic evidence delta 偵測。

必要工作：

- 在同一受影響 window 或 dependency neighborhood 內比對新舊 evidence 記錄。
- 偵測 added evidence。
- 偵測 removed evidence。
- 偵測 wording 改變但 semantically unchanged 的 evidence。
- 當 claim 語意改變時，偵測 modified evidence。
- 產生 downstream invalidation 可消費的結構化 delta 物件。

Semantic equivalence cascade 應包含：

- Canonical-form matching。
- Embedding similarity。
- 對 ambiguous case 可選的 LLM judge。

實作應暴露 similarity 與 judge confidence 的可設定 threshold。

預期輸出：

- `added_evidence`。
- `removed_evidence`。
- `unchanged_evidence`。
- `modified_evidence`。
- Match confidence scores。
- 供除錯用的 explanation 或 trace metadata。

## 9. M2：Cost-Aware Invalidation Policy

實作決定每個受影響 KG object 應 skip、patch 或 rebuild 的策略。

必要工作：

- 將 evidence delta 對應到受影響的 entity 與 relationship。
- 估計每個 delta 的 impact size。
- 估計每個受影響物件的 refresh cost。
- 若可用，納入 query frequency。
- 納入 staleness tolerance 或 freshness 需求。
- 為每個物件產生 refresh decision。
- 持久化 decision、rationale、estimated cost 與 resulting freshness state。

可選工作：

- 將 relationship delta 傳播到 community。
- 決定受影響的 summary 與 embedding 應 skip、patch 或 rebuild。

支援的 decision：

- `SKIP`：保持物件不變，更新 freshness metadata。
- `PATCH`：套用 local update，不進行完整 regeneration。
- `REBUILD`：從目前 source evidence 重新產生物件。

策略應同時支援 simple threshold rules 與供 sensitivity analysis 使用的 calibrated parameters。

## 10. Incremental Graph Update Execution

實作依 refresh decision 更新圖的 execution 層。

必要工作：

- 插入 added evidence，移除或 deactivate removed evidence。
- 更新受 evidence delta 影響的 entity 與 relationship link。
- 當 evidence 改變時，patch 或 rebuild derived relationship。
- 每次 decision 後更新 freshness metadata。
- 保留足夠版本歷史，以解釋物件為何 fresh、stale、skipped、patched 或 rebuilt。

可選工作：

- 必要時 patch 或 rebuild 受影響的 community assignment。
- 必要時 patch 或 rebuild summary。
- 對 semantic content 改變的物件 refresh embedding。

每個 update step 後的 graph state 應可被查詢與稽核。

## 11. M3：Freshness-Aware Query 與 Lazy Refresh

將 query-time freshness 控制視為 stretch goal。最小專案只需持久化 freshness metadata，並展示 corpus update 後 KG object 為 fresh、stale、skipped、patched 或 rebuilt。

Stretch 工作：

- 允許查詢指定 freshness 需求，例如 `fresh_only=true` 或 `max_staleness`。
- 以 per-object freshness metadata 檢索 graph 與 summary 結果。
- 當查詢要求 fresh 結果時，排除 stale 物件。
- 當 stale 物件被查詢且允許 refresh 時，觸發 lazy refresh。
- 將非 urgent 的 stale 物件放入 background refresh queue。
- 在查詢結果中回傳 staleness annotation。

此機制應讓 freshness 對 downstream application 可見，而非隱藏 stale 衍生資料。

## 12. Baseline 系統

實作或整合所需 baselines。

必要 baselines：

- 在選定 DocRED/Re-DocRED subset 上的 full corpus-to-KG rebuild。
- Generic ArangoDB AQL traversal invalidation。
- Naive chunk-to-entity 或 sentence-to-edge invalidation。
- 提案的 evidence-level IVM 系統。

若時間允許的可選 baselines：

- 使用 BM25 與 dense retrieval 的 Vanilla RAG。
- Microsoft GraphRAG full rebuild。
- LightRAG 風格、不 refresh summary 的 graph-level incremental update。

Baseline 要求：

- 使用相同 corpus split。
- 在適用處使用相同 extraction 設定。
- 記錄可比的 token cost、latency、graph delta、invalidation 與 freshness 指標。
- 使每個 baseline 可透過相同 experiment harness 執行。

## 13. Evaluation Harness

實作統一的 demo 與 experiment runner。

必要工作：

- 在 synthetic edit sequence `T0..Tn` 上執行每個 system。
- 每個 update step 後執行 graph update 與 provenance 檢查。
- 每個 update step 後對持久化 KG object 執行 freshness-state 檢查。
- 僅在 demo 包含 retrieval 時執行 retrieval 檢查。
- 收集 token 用量與 API cost。
- 收集 wall-clock latency。
- 儲存 intermediate output 以供除錯。
- 以結構化格式儲存 final metrics。
- 為 demo 與 final report 產生視覺化。

Harness 應能以單一命令重現每個報告結果。

## 14. 指標

實作以下指標：

- Refresh cost：LLM token 乘以 model 價格。
- Graph delta correctness：added、removed、modified KG edge 是否符合 synthetic corpus edit 的預期效果。
- Staleness rate：maintenance decision 後仍 stale 的 affected graph object 比例。
- Invalidation precision：被選中 affected object 中，真正受影響的比例。
- Invalidation recall：真正 affected object 中被系統選中的比例。
- Over-invalidation ratio：affected set size 除以 generic AQL traversal affected set size。
- Storage 與 index overhead。

可選指標：

- Query latency 分布。
- Background refresh queue 長度。
- Summary 與 embedding refresh cost。

主要比較應展示 synthetic corpus edit 上的 update cost、invalidation precision、over-invalidation 與 graph freshness。

## 15. Oracle 與除錯支援

實作以更強參考驗證 incremental corpus-to-KG 行為的支援。

必要工作：

- 對選定 update step 執行 full re-extraction 或 full rebuild。
- 將 incremental delta 與 full rebuild delta 比較。
- 人工檢查 sampled evidence match 與 mismatch。
- 儲存 semantic evidence matching decision 的 trace。
- 儲存 invalidation decision 的 trace。
- 提供從 KG edge 回溯到 source evidence sentence 的 provenance 檢查工具。

此支援用於評估系統是否同時避免 under-invalidation 與 over-invalidation。

## 16. Demo 與檢查介面

實作展示系統的輕量介面。

必要能力：

- 選擇 corpus update step。
- 套用或選擇 synthetic corpus edit。
- 顯示 changed document、sentence 或 span。
- 顯示新舊 evidence。
- 顯示 semantic evidence delta 結果。
- 顯示受影響的 KG node、edge 與 provenance link。
- 顯示 `SKIP`、`PATCH` 與 `REBUILD` decision。
- 顯示 maintenance 前後的 graph state。
- 顯示 graph object 上的 freshness annotation。
- 顯示跨 update step 的 metric 趨勢。
- 顯示從 KG edge 回溯到 source evidence 的 provenance。

可選能力：

- 顯示受影響的 summary 與 embedding。
- 顯示從 summary 回溯到 source evidence 的 provenance。

若 notebook 能支援相同 inspection workflow 即足夠。若需要互動式視覺化，可使用 Streamlit app。

## 17. 可重現性與封裝

實作專案封裝，使完整系統可重跑。

必要工作：

- 提供環境設定說明。
- 提供 ArangoDB 啟動設定。
- 提供 corpus 下載或 preprocessing 命令。
- 提供執行實驗的單一命令或 script。
- 當完整 LLM run 成本過高時，提供 cached sample output。
- 提供 model 選擇、threshold、selected document、synthetic edit script 與 baseline 的設定檔。
- 提供清楚的 README 文件。
- 提供 LLM API 錯誤與 partial experiment run 的 failure-handling 行為。

## 18. 完成標準

當實作能完成以下項目時，專案即視為完成：

- 從 `T0` 建構初始 corpus-to-KG 狀態。
- 至少端到端套用一次 synthetic corpus edit incrementally。
- 執行 semantic evidence delta detection。
- 執行 cost-aware invalidation。
- 依 refresh decision 更新 KG entity 與 relationship。
- 將 evidence、delta、invalidation、refresh decision 與 freshness metadata 持久化在 ArangoDB。
- 在相同 edit sequence 上執行選定 baseline。
- 產出所需 metrics。
- 將 incremental maintenance 與 full rebuild 行為比較。
- 展示 corpus edit 正確更新 underlying KG 與 provenance state。
- 產生主要的 cost、invalidation 與 freshness 圖表。
- 從文件化命令重現結果。

Stretch 完成標準：

- 將 update 傳播到 community。
- Refresh 或 invalidate summary 與 embedding。
- 以 freshness metadata 提供 query 服務。
