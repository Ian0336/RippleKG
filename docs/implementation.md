# 基於 IVM 的 GraphRAG 實作工作

本文件依 `thought.md` 的設計決策展開實作工作。**`thought.md` 是設計細節的真理來源**（collection 名稱、欄位、update path、decision rule）；本文件補充「要建構、執行、量測與交付的內容」，但不另創設計。任何不一致處以 `thought.md` 為準。

## 1. 專案目標

為 LLM 生成的知識圖建構增量維護層，使 corpus 更新能在不進行完整重建的情況下，傳播到持久化於 ArangoDB 的 KG。

系統應改善：

- 相較完整 corpus-to-KG 重建，降低 refresh 成本。
- 相較 naive sentence 層級或 generic graph traversal invalidation，提高 invalidation 精確度。
- 為 evidence delta、refresh decision 與 freshness metadata 提供持久化 DB 狀態。

定位是**期末專案**：目標是把 pipeline 做出來、能 demo；不追求嚴謹 baseline 比較或論文級 evaluation。Community、summary、embedding、query-time lazy refresh 全部歸 stretch。

## 2. 端到端系統流程

實作管線應支援以下流程。Step 1–5 為**同步脊椎**，在單一 ArangoDB transaction 內完成；Step 6 為可延後的 refresh 執行。

1. 載入由 DocRED / Re-DocRED 建構的 `T0` corpus-to-KG state。
2. 套用 synthetic edit step `T1..Tn`（每步含 `intended_triples`）。
3. 偵測 changed sentence（依 `text_hash`）。
4. 沿 outbound 1-hop provenance edge 找 affected mention / relation evidence。
5. 以 canonical triple 比對舊 evidence 與 `intended_triples`，產生 `evidence_deltas`（M1），同步更新 provenance edge，產生 `refresh_decisions`（M2），標記 stale。
6. 執行 `apply_refreshes`：immediate 模式立刻完成；deferred 模式可留待後續 tick。
7. 從 `evidence_deltas` / `refresh_decisions` log 出指標。

## 3. Corpus 與更新時間軸

實作 DocRED / Re-DocRED 的可重現 corpus loader。資料集作為初始 corpus-to-KG pair：

- `sents` → `sentences`。
- `vertexSet` → `entities` + `mentions` edge。
- `labels` → `relations`（document collection，`head` / `tail` 為 entity `_key`）。
- `labels.evidence` → `sentence_supports_relation` edge。

必要工作：

- 下載並正規化 DocRED 或 Re-DocRED。
- 保留 document title、sentence text、entity mention、entity type、relation type、evidence sentence index。
- 將每份 document 轉為 deterministic `_key`，每句轉為 `{doc_id}:{idx}`。
- 從原始 dataset 建構 `T0` 圖（直接寫入 8 個 collection，不跑抽取）。
- 將每個 synthetic edit 儲存為 structured metadata，使 demo 可重現。

每筆 synthetic edit 至少包含：

- `doc_id`, `sent_idx`, `new_text`, `step`。
- `intended_triples`：該句編輯後預期支撐的 triple set（選項 A，主路徑；可為空）。

Demo workload 至少包含三種 edit，剛好打到 M1/M2 的三條決策路徑：

1. **改變 relation 的 edit** → evidence delta = removed + added → M2 = REBUILD/PATCH。
2. **只 paraphrase 的 edit**（triple 不變、文字變）→ delta = unchanged → M2 = **SKIP**。
3. **non-evidence sentence 的 edit**（不支撐任何 edge）→ 不觸及任何 KG 物件。

## 4. Evidence 來源

**第一版主路徑為選項 (A) authored triples**：synthetic edit 直接帶 `intended_triples`，不跑抽取。理由：extraction 不是貢獻核心，authored triples 讓 M1 有確定的「新 evidence」可比對，extraction 品質不會變成主要工作。

可選 (B)：對 edited sentence 跑輕量 LLM extractor 產生 `intended_triples`，介面與 (A) 相同。有空再加。

`T0` 的 evidence 直接用 DocRED `vertexSet` / `labels` / `labels.evidence` annotation。

Entity resolution 用 `entities.norm_name`（normalized name）做 deterministic 比對。

## 5. ArangoDB 儲存模型

實作為單一 ArangoDB 資料庫，**共 8 個 collection**。欄位真理來源為 `thought.md` §6–§7。

Document collections（KG 物件，4 個）：

- `documents`
- `sentences`（原子單位）
- `entities`
- `relations`（document collection，非 edge；以欄位 `head` / `tail` 指向 entity `_key`）

Edge collections（provenance；ripple 沿此走，2 個）：

- `mentions`：`sentences ─▶ entities`
- `sentence_supports_relation`：`sentences ─▶ relations`

Document collections（維護，2 個）：

- `evidence_deltas`（M1 輸出）
- `refresh_decisions`（M2 輸出，含 `status: pending | applied`）

**不建**：chunks、extraction_windows、evidence_items、invalidations、object_freshness、query_logs、experiment_metrics、communities、summaries、embeddings 與其 edge。freshness 化為 `entities` / `relations` 上的欄位；歷史靠 `evidence_deltas` / `refresh_decisions` log 還原，不保留多版本（無 bitemporal）。

`current_step` 由 manager 在記憶體持有或寫進小 meta doc，不另設 epoch 集合。

必要索引（見 `thought.md` §9）：

- `sentences`：`(doc_id, idx)` 複合；`text_hash`。
- `entities`：`freshness_status`。
- `relations`：`head`；`tail`；`freshness_status`。
- `mentions`：`_from`；`_to`；`status`。
- `sentence_supports_relation`：`_from`；`_to`；`status`。
- `evidence_deltas`：`step`；`target_id`。
- `refresh_decisions`：`step`；`status`；`target_id`。

## 6. 初始 T0 建構

實作 `T0` 完整初始建構，**直接由 annotation 寫入，不跑 LLM**：

- 匯入 DocRED / Re-DocRED documents 為 `documents` + `sentences`。
- 從 `vertexSet` 解析 `entities`（含 `norm_name`），每個 mention 寫一條 `mentions` edge（`_from = sentences/{sent}`, `_to = entities/{entity}`）。
- 從 `labels` 寫 `relations` doc（`head` / `tail` / `rel_type`）。
- 從 `labels.evidence` 寫 `sentence_supports_relation` edge。
- 初始化 `entities.evidence_count` 與 `relations.evidence_count`。
- 所有物件 `freshness_status = fresh`，`last_changed_step = 0`。

`T0` 是後續 incremental KG maintenance 的起點，也是可選 full-rebuild 對照所需的參考行為。

## 7. Change Detection

對每筆 synthetic edit：

- 讀舊 `sentences/{doc_id}:{sent_idx}`，比對 `text_hash`。
- 若改變：in-place 覆寫 `text` / `text_hash` / `last_changed_step = step`（舊文字不另存版本；變化記在 `evidence_deltas`）。
- 從 `sentences/{sent}` 走 outbound 1-hop：
  - `mentions` → 舊 mention evidence。
  - `sentence_supports_relation` → 舊 relation evidence。

這組「舊 evidence」即 M1 的左邊。整個 edit step 在單一 ArangoDB transaction 內完成。

## 8. M1：Semantic Evidence Delta

實作舊 evidence set 與 `intended_triples`（新 evidence）的 delta 偵測。規則見 `thought.md` §10.4。

**Relation evidence**：以 canonical triple `(head, rel_type, tail)` 為 key。

- 新有舊無 → `added`
- 舊有新無 → `removed`
- 新舊都有 → `unchanged`（即使 sentence 文字改了 → paraphrase，可 SKIP）

**Mention evidence**：以 `entity.norm_name` 為 key，同樣分 `added` / `removed` / `unchanged`。

每筆變化寫入 `evidence_deltas`，欄位為 `step`, `sent_id`, `delta_type`, `scope`, `triple`, `target_id`, `reason`。

同時同步更新 provenance edge（脊椎，必須在同 transaction）：

- `added` → 新增對應 `sentence_supports_relation` / `mentions` edge（必要時新建 relation / entity）。
- `removed` → edge `status = removed`, `removed_step = step`。
- `unchanged` → edge 不動。

可選 semantic 增強：對 relation 文字加 embedding 相似度，讓「文字不同但語意同」歸到 unchanged。骨架仍是純 canonical-triple 規則，保持 deterministic。

## 9. M2：Cost-Aware Invalidation Policy

對每個 affected entity / relation，依 `thought.md` §10.5 的 decision table：

```
triple unchanged（只改寫）               -> SKIP
evidence 減少但仍有其他 active evidence  -> PATCH
最後一條 active evidence 被移除          -> REBUILD
evidence 新增                            -> PATCH（或新建物件）
```

名目 cost：`SKIP = 0`、`PATCH = 1`（DB 寫）、`REBUILD = 1 LLM call`（authored triples 模式下實際為 DB 寫，cost 仍記為 LLM call 以便對照）。

每個決策寫入 `refresh_decisions`，`status = pending`。

PATCH / REBUILD 對象：`freshness_status = stale`、`last_changed_step = step`；SKIP 不動。

到此 pipeline 脊椎結束：**即使後面一個 refresh 都不跑，DB 狀態仍自洽**。

## 10. Refresh 執行：同步預設 + 可選漸進

實作 `apply_refreshes(step or tick)`，見 `thought.md` §11。**沒有獨立背景 worker / lease / retry / recovery**。

兩種模式：

- **immediate（預設）**：M2 後立刻處理該 step 所有 `pending` decision，一個 edit step 結束時所有物件回 `fresh`。
- **deferred（demo 用）**：把部分 decision（例如所有 REBUILD）留在 `pending`，之後 tick 呼叫 `apply_refreshes()`。畫面上會看到 stale 跨 step。

執行：

- `PATCH` → 依目前 active evidence 重算 `evidence_count` / weight。
- `REBUILD` → 依目前 active evidence 重解析 entity / 刪除無 evidence 的 relation。
- 完成 → 物件 `freshness_status = fresh`，`decision.status = applied`。

延後安全的 invariant 見 `thought.md` §11.3：脊椎永遠同步、`apply_refreshes` idempotent、stale 可見。

## 11. Freshness 可見性（Lazy Refresh 為 Stretch）

**必要**：物件帶 `freshness_status`，查詢可：

- `fresh_only = true` → 過濾 stale 物件。
- 預設 → 回傳全部，stale 物件附 freshness 旗標。

**Stretch**：

- query-time lazy refresh（讀到 stale 觸發 `apply_refreshes`）。
- `max_staleness = K`。
- staleness annotation 回傳。
- 背景 refresh queue（**只有 stretch 才碰**；核心不做背景 worker）。

## 12. Baseline 系統（皆為可選，sanity check 與對照用）

期末專案不追求嚴謹 baseline 評估。下列在有時間時做：

- **B0 Full rebuild**：每次 edit 後從 edited corpus state 重建整張 KG。用途：正確性 sanity check，比對我們 patch / rebuild 後是否等於 full rebuild。
- **B1 Generic AQL traversal**：從 changed sentence 沿 reachability 把所有可達物件失效。用途：對照 over-invalidation。
- **B2 Naive invalidation**：changed sentence → 提到的 entity / edge 全標 stale，不算 evidence delta。用途：對照「跳過 evidence-delta」的代價（少了 SKIP）。

**主打對照**（若做）：**B2 vs ours** —— 同一句被改寫但 triple 不變時，我們 SKIP、B2 仍失效。

下列 baseline 不做：Vanilla RAG、Microsoft GraphRAG、LightRAG。

## 13. 指標（從 log 統計，不寫獨立 harness）

**不另寫評估 harness**。直接從 `evidence_deltas` 與 `refresh_decisions` 統計。

每個 edit step：

- `added` / `removed` / `unchanged` evidence 筆數。
- `SKIP` / `PATCH` / `REBUILD` 決策數。
- 名目 cost 總和，對照「全部 REBUILD」的 cost（= 省下多少）。
- 被標 stale 的物件數（deferred 模式下，跨 step 的 stale 曲線）。

可選指標（若做 B1）：

- Over-invalidation ratio：我們 affected set size / B1 reachable set size。

不做：query latency 分布、storage / index overhead 量測、token cost API tracking、background queue length、invalidation precision / recall 嚴格量測。

## 14. Demo

實作展示系統的輕量介面。

必要能力：

- 選擇 corpus update step。
- 套用或選擇 synthetic edit。
- 顯示 changed sentence。
- 顯示舊 evidence 與 `intended_triples`。
- 顯示 `evidence_deltas` 結果。
- 顯示受影響的 KG entity / relation 與 provenance edge。
- 顯示 `SKIP` / `PATCH` / `REBUILD` decision。
- 顯示 maintenance 前後的 graph state（before / after KG 圖）。
- 顯示 freshness annotation。
- 顯示 §13 的數字。

**Notebook 介面足夠**。互動式視覺化（Streamlit / 既有 frontend）為加分項，依 grading 策略**最後再做**。

## 15. 可重現性

- 提供環境設定說明（Python venv + ArangoDB docker-compose）。
- 提供 corpus 下載與 preprocessing 命令。
- 提供執行 demo 的單一 script（套用所有 synthetic edit、輸出 log 數字）。
- 提供 synthetic edit 設定檔（含 `intended_triples`）。
- 提供 README 文件。

## 16. 完成標準

最小可行範圍（對齊 `thought.md` §15）：

- 8 個 collection 與 §5 索引建好。
- DocRED / Re-DocRED loader 建出 `T0` 圖。
- Synthetic edit loader（含 `intended_triples`）。
- §7–§9 同步更新主幹：edit → affected evidence → M1 delta → M2 decision → freshness。
- §10 refresh 執行（immediate 必做；deferred 漸進模式建議做）。
- 從 log 出 §13 數字。
- Demo 展示一次 edit 的 before / after KG 與 delta / decision log。

Stretch：

- §11 lazy refresh、`max_staleness`、staleness annotation。
- B0 / B1 / B2 對照圖。
- Community / summary / embedding。
- 互動式 demo frontend。
- 選項 (B) LLM extractor。

## 17. 實作里程碑（對齊 thought.md §16）

- **M1 Schema**：建 8 個 collection 與 §5 索引。
- **M2 Ingestion**：DocRED loader → `T0` 圖。
- **M3 Edit + M1**：synthetic edit application、`evidence_deltas`、同步更新 provenance edge。
- **M4 M2 + Freshness**：decision table → `refresh_decisions`、標 stale。
- **M5 Refresh**：`apply_refreshes`（immediate + deferred）。
- **M6 Demo**：before / after 圖、log 數字、（可選）B0 / B1 / B2 對照。
