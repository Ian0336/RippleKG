# 基於 ArangoDB 的 IVM：M1/M2 實作補充文件（精簡版）

## 0. 文件角色

本文件是 `proposal.md` 的實作補充，是**設計細節的真理來源**（collection 名稱、欄位、update path、decision rule）。

它記錄期末專案要實作的東西：以 ArangoDB 為唯一持久層，把 `corpus -> KG` 的維護路徑端到端跑通，核心是 **M1（semantic evidence delta）** 與 **M2（cost-aware invalidation policy）**。

定位是**期末專案**：目標是把這條 pipeline 做出來、能 demo，而不是發論文。因此 schema 刻意砍到最小、評估只取 log 免費送的數字、不追求嚴謹 baseline 比較。M1、M2 都要做，沒有誰比較重要。

> **本版相對舊版的主要簡化**
> - 原子單位收斂到 **sentence**：拿掉 `chunks` 與 `extraction_windows` 兩層。
> - 拿掉 bitemporal versioning（`valid_from/valid_to/version/source_epoch/maintained_epoch`），改用單一整數 `step` + 物件上的 `freshness_status` 欄位 + in-place 更新 + delta/decision log 當 audit trail。
> - 拿掉背景 worker / lease-based task queue：refresh **預設同步執行**，並提供一個**可選的延後執行**模式來做「漸進維護」的畫面（見 §11），且保證不破壞 pipeline。
> - community / summary / embedding 全部歸 stretch，不在核心。
> - 集合從 ~25 個縮到 **4 物件 + 2 provenance edge + 2 維護 = 8 個**。

## 1. 必要的 Corpus-to-KG 路徑

```text
corpus edit
  -> changed sentence
  -> affected evidence（該 sentence 上的 mentions / relation evidence）
  -> semantic evidence delta            （M1）
  -> affected entity / relation
  -> SKIP / PATCH / REBUILD decision     （M2）
  -> persisted KG / provenance / freshness state in ArangoDB
```

每個 stage 都把 output 物化到 ArangoDB collection 或 edge collection，downstream stage 消費持久化記錄，而非 in-memory object。這就是滿足資料庫要求的關鍵：pipeline 的每一站都在 DB 留下狀態。

## 2. 實作動機

研究問題：

> 純 ArangoDB 系統能否在原生多模型資料庫之上，加入 evidence-aware 的 invalidation 語意與 freshness 追蹤，使得 corpus 改動時只重算「evidence 真正改變」的 KG 物件，而非沿 reachability 把所有可達物件都失效？

持久化在 ArangoDB 的狀態（精簡版）：

```text
KG 物件          entities, relations
provenance       sentences, mentions, sentence_supports_relation
evidence delta   evidence_deltas
refresh decision refresh_decisions
freshness        entities / relations 上的 freshness_status 欄位
```

貢獻在邏輯資料庫層：provenance schema、evidence-delta 計算、cost-aware refresh 決策、交易式 in-place 更新與可見的 freshness 狀態。

### 2.1 資料集與 Demo Workload

使用 **DocRED 或 Re-DocRED**，因為它已含精簡的 corpus-to-KG 對應：

```text
sents          -> sentences（source corpus，sentence 即原子單位）
vertexSet      -> entities + mentions（每個 mention 落在某個 sentence）
labels         -> relations（head / tail / relation_type）
labels.evidence-> 每條 relation 的 sentence-level provenance（免費送的！）
```

`labels.evidence` 直接給我們「哪幾句 sentence 支撐這條 edge」，這正是 `sentence_supports_relation` 這條 provenance edge 的內容，也是整個 ripple 的起點。

**更新模型**：synthetic edit，不是 natural time-series。

```text
T0 = 原始 DocRED document + annotated KG
T1 = 手動編輯一句 sentence（連帶 author 好預期的新 triple，見 §10）
T2 = 另一次 controlled edit
...
Tn = 最終 edited corpus state
```

Demo 至少應包含三種 edit，剛好打到 M1/M2 的三條決策路徑：

1. **改變 relation 的 edit** → evidence delta = removed + added → M2 = REBUILD/PATCH。
2. **只 paraphrase 的 edit**（triple 不變、文字變）→ delta = unchanged → M2 = **SKIP**（這是 evidence-aware 的賣點）。
3. **non-evidence sentence 的 edit**（不支撐任何 edge）→ 不觸及任何 KG 物件。

## 3. 主要假設

> 完全建構在 ArangoDB 之上的 evidence-aware 維護層，可透過區分 reachability 與 semantic invalidation，減少 over-invalidation 與 recomputation。

```text
generic traversal（baseline）:
  changed object -> 所有可達 derived objects

evidence-aware（本專案）:
  changed sentence -> 直接支撐它的 mentions / relation evidence
                   -> 計算 evidence delta
                   -> 只動 maintained state 可能 stale 的物件
```

## 4. 為何純 ArangoDB 合理

- 完整可運作系統、單一持久層、易於檢查除錯、課程時程內可完成。
- 取捨：無法控制實體 storage layout / RocksDB，因此 DBMS 貢獻框架化為**邏輯資料庫設計** — provenance schema、evidence-based 記錄、AQL provenance traversal、交易式 invalidation 記錄、可見 freshness 狀態。

## 5. 系統架構

```text
Maintenance Manager（Python）
  -> ArangoDB document collections   （documents / sentences / entities / relations）
  -> ArangoDB edge collections       （mentions / sentence_supports_relation）
  -> ArangoDB maintenance collections（evidence_deltas / refresh_decisions）
  -> AQL queries / 1-hop provenance traversal
  -> ArangoDB transactions（單一 edit step 內的更新為一個 transaction）
```

Manager 負責：apply edit、找 affected evidence、M1 算 delta、M2 下決策、寫 freshness、執行（或延後）refresh。**沒有獨立的背景 worker**；refresh 是 manager 的一個同步步驟，可選擇延後（§11）。

## 6. ArangoDB 集合（共 8 個）

```text
物件（document collections）
  documents
  sentences
  entities
  relations

provenance（edge collections）— 這是 ripple 沿著走的圖
  mentions                     sentences ─▶ entities    （帶 surface text）
  sentence_supports_relation   sentences ─▶ relations   （帶 relation_type）

維護（document collections）— pipeline 每一站的落地點
  evidence_deltas              M1 輸出
  refresh_decisions            M2 輸出（含 pending/applied 狀態）
```

說明：

- **`mentions` 與 `sentence_supports_relation` 是 edge collection**，所以 provenance 是一張真的圖。從一句 changed sentence 出發，**outbound 1-hop** 就是 affected entities 與 affected relations — 這就是 affected set 的計算，不需要多跳 reachability traversal。
- `relations` 用 document collection（不是 edge），`head`/`tail` 存 entity `_key`。KG 圖用 relations docs 重建（head→tail）即可，demo 渲染直接讀這張表。
- 一個極小的 `meta` doc 存 `current_step`（或由 manager 在記憶體持有並寫進每筆記錄），不另設 epoch 集合。
- community / summary / embedding 與其 edge 全部不建（stretch）。

## 7. 物件模型（精簡欄位）

沒有 bitemporal。每個 KG 物件帶一個 `freshness_status` 與 `last_changed_step`；歷史靠 `evidence_deltas` / `refresh_decisions` log 還原，不保留多版本。

```text
documents
  _key            # doc_id
  title
  num_sents
  source          # "docred" | "re-docred"

sentences         # 原子單位
  _key            # 例如 "{doc_id}:{idx}"
  doc_id
  idx
  text
  text_hash       # 偵測 changed sentence 用
  status          # active | removed
  last_changed_step

entities          # KG node
  _key            # entity_id
  name
  norm_name       # entity resolution 用 normalized name（保持 deterministic）
  type
  evidence_count  # 由幾個 mention 支撐
  freshness_status# fresh | stale
  last_changed_step

relations         # KG edge（存為 document）
  _key            # relation_id
  head            # entity _key
  tail            # entity _key
  rel_type
  evidence_count  # 由幾句 sentence 支撐
  freshness_status# fresh | stale
  status          # active | removed
  last_changed_step
```

Edge collections：

```text
mentions                     # sentence ─▶ entity，等同「這句話提到這個 entity」的 evidence
  _from  sentences/{sent}
  _to    entities/{entity}
  surface                    # 表面文字
  status                     # active | removed
  added_step / removed_step

sentence_supports_relation   # sentence ─▶ relation，DocRED labels.evidence
  _from  sentences/{sent}
  _to    relations/{relation}
  rel_type
  status                     # active | removed
  added_step / removed_step
```

維護 collections：

```text
evidence_deltas              # M1 輸出，每筆 = 一個 evidence 變化
  _key
  step
  sent_id                    # 哪句 sentence 造成的
  delta_type                 # added | removed | unchanged
  scope                      # mention | relation
  triple                     # { head, rel_type, tail }（relation）或 { entity, surface }（mention）
  target_id                  # 對應的 entity / relation _key
  reason

refresh_decisions            # M2 輸出
  _key
  step
  target_type                # entity | relation
  target_id
  decision                   # SKIP | PATCH | REBUILD
  reason
  cost                       # 名目成本（SKIP=0, PATCH=1, REBUILD=LLM_call）
  status                     # pending | applied   ← 支援 §11 的漸進維護
```

## 8. Provenance Schema

```text
Sentence ─(mentions)──────────────▶ Entity
Sentence ─(sentence_supports_relation)▶ Relation
Relation.head / Relation.tail ─────▶ Entity   （以欄位表示，非 edge）
```

刻意避免 naive 的 `Sentence -> Entity 直接失效`。改用 evidence 化的兩條 edge：一句 sentence 改了，要先看它**支撐**了哪些 mention / relation，再由 evidence delta 決定下游 entity/relation 是否真的受影響。surface 文字變但 triple 不變時，這層讓我們能 SKIP。

## 9. ArangoDB 索引

只建實際 query 會用到的：

```text
sentences        : doc_id, idx（複合）；text_hash
entities         : _key（內建）；freshness_status
relations        : _key（內建）；head；tail；freshness_status
mentions         : _from（找某句的 mentions）；_to（找某 entity 的 mentions）；status
sentence_supports_relation : _from；_to；status
evidence_deltas  : step；target_id
refresh_decisions: step；status；target_id
```

支援的核心查詢：

```text
給定 changed sentence -> 找 affected entities / relations   （走 _from index，1-hop）
找所有 stale 物件                                            （freshness_status index）
找 pending 的 refresh decision                              （status index）
依 step 重播 / 檢視 delta 與 decision                        （step index）
```

## 10. 更新路徑（同步主幹）

這是 pipeline 的脊椎，**永遠同步、在單一 transaction 內完成**（§11 只允許「aggregate 修復」延後，不允許動這條脊椎）。

### 10.1 輸入

```text
{ doc_id, sent_idx, new_text, intended_triples, step }
```

`intended_triples` 來自**選項 (A)**：author edit 時連帶寫好這句編輯後預期支撐的 triple set（可為空）。這讓 M1 有確定的「新 evidence」可比對，extraction 品質不會變成主要工作。選項 (B)（對 edited sentence 跑輕量 LLM extractor 產生 `intended_triples`）作為有空再加的替代來源，介面相同。

### 10.2 Step 1：套用 edit

```text
讀舊 sentence -> 比對 text_hash
若改變：
  覆寫 sentences.text / text_hash / last_changed_step = step
  （in-place；舊文字不另存版本，變化記在 evidence_deltas）
```

### 10.3 Step 2：找 affected evidence

```text
從 sentences/{sent} 走 outbound 1-hop：
  mentions                   -> 舊的 mention evidence（含 surface / 指向哪個 entity）
  sentence_supports_relation -> 舊的 relation evidence（含 rel_type / 指向哪個 relation）
```

這組「舊 evidence」就是 M1 的左邊。

### 10.4 Step 3：M1 — 計算 semantic evidence delta

把舊 evidence 與 `intended_triples`（新 evidence）做集合比對：

```text
relation evidence：以 canonical triple (head, rel_type, tail) 為 key
  新有舊無            -> added
  舊有新無            -> removed
  新舊都有            -> unchanged（即使 sentence 文字改了 → paraphrase，可 SKIP）

mention evidence：以 (entity norm_name) 為 key，同樣分 added / removed / unchanged
```

每筆變化寫入 `evidence_deltas`。可選的「semantic」增強：對 relation 文字額外做一個 embedding 相似度判斷，把「文字不同但語意同」也歸到 unchanged；骨架仍是純 canonical-triple 規則，保持 deterministic。

同時把 provenance edge 更新到一致（這屬於脊椎，必須同步）：

```text
added    -> 新增對應的 sentence_supports_relation / mentions edge（必要時新建 relation/entity）
removed  -> 把對應 edge status = removed, removed_step = step
unchanged-> edge 不動
```

### 10.5 Step 4：M2 — cost-aware invalidation decision

對每個 affected entity / relation，依 evidence delta 查 decision table：

```text
triple unchanged（只改寫）                 -> SKIP
  （freshness 不變，cost = 0）

evidence 減少，但物件仍有其他 active evidence -> PATCH
  （重算 evidence_count / weight，cost = 1 DB 寫）

最後一條 active evidence 被移除             -> REBUILD
  （重解析 entity 或刪 relation，cost = 1 LLM call）

evidence 新增                              -> PATCH（或新建物件）
```

每個決策寫入 `refresh_decisions`（`status = pending`）。「cost-aware」就是這張表：能 PATCH 就不 REBUILD，能 SKIP 就不動，並把名目 cost 記下來。

### 10.6 Step 5：標記 freshness

```text
被判 PATCH / REBUILD 的 entity / relation：
  freshness_status = stale
  last_changed_step = step
被判 SKIP 的：不動
```

到這裡，pipeline 的脊椎已完成：corpus 改動已經正確傳播成 evidence delta、decision 與可見的 stale 狀態，**即使後面一個 refresh 都不跑，DB 狀態也是自洽的**。

## 11. Refresh 執行：同步預設 + 可選漸進

這一節取代舊版的「背景 worker」。沒有 lease、沒有 retry queue、沒有 recovery。

### 11.1 兩種模式

```text
immediate（預設）:
  Step 5 之後立刻處理該 step 所有 pending decision
  -> 一個 edit step 結束時，所有物件都回 fresh

deferred（demo 漸進維護用）:
  把部分 decision（例如所有 REBUILD）留在 pending
  之後在某個「maintenance tick」呼叫 apply_refreshes() 再處理
  -> 畫面上會看到某些 edge 跨 step 維持 stale，tick 後才轉 fresh
```

### 11.2 apply_refreshes(step or tick)

```text
取 refresh_decisions where status = pending（可只取某類）
逐筆：
  PATCH   -> 依「目前」active evidence 重算 evidence_count / weight
  REBUILD -> 依目前 active evidence 重解析 entity / 刪除無 evidence 的 relation
  完成    -> 物件 freshness_status = fresh
            decision.status = applied
```

### 11.3 為何延後「不會破壞 pipeline」（核心 invariant）

```text
同步保證（脊椎，永不延後）：
  sentences / mentions / sentence_supports_relation 的 evidence 狀態
  + entities / relations 的「存在與否」
  在每個 edit step 內 atomically 更新到一致。

可延後（只有這個）：
  derived aggregate 的修復 —— evidence_count / weight / 重解析 / freshness 翻回 fresh。
```

延後安全的三個理由：

1. **下一個 edit 的 M1 不依賴 aggregate** — delta 是從 sentence-level evidence 比對算的，aggregate 沒跟上不影響正確性。
2. **apply_refreshes 是 idempotent** — 永遠依「目前」active evidence 重算；重跑、晚跑、跑兩次結果相同；中途又被新 edit 改了也會自我修正。
3. **stale 是可見的** — 沒修的物件明確標 `freshness_status = stale`，是「已知未刷新」，不是靜默錯誤。查詢端可據此選擇（§12）。

所以：**pipeline 正確性只依賴 §10 的同步脊椎；deferred 只改變 aggregate/freshness「何時追上」**，這正好就是想要的漸進維護畫面。

## 12. 查詢語意（精簡）

物件帶 `freshness_status`，查詢可：

```text
fresh_only = true        -> 過濾掉 stale 物件
（預設）                  -> 回傳全部，stale 物件附 freshness 旗標
```

Query-time lazy refresh（讀到 stale 就觸發 apply_refreshes）列為 stretch。核心只要「freshness 可見、可被查詢條件用到」即可。

## 13. 基準（評估弱化，皆為可選）

期末專案不追求嚴謹評估。下列只在有時間時做，主要當 sanity check 與 demo 對照：

```text
B0 Full rebuild     : 每次 edit 後從 edited corpus 重建整張 KG。
                      用途：correctness sanity —— 檢查我們 patch/rebuild 後的 KG
                      是否等於 full rebuild 的結果。
B1 Generic traversal: 從 changed sentence 沿 reachability 把所有可達物件失效。
                      用途：對照 over-invalidation（幾乎必然比我們大）。
B2 Naive invalidation: changed sentence -> 直接把提到的 entity/edge 全標 stale。
                      用途：對照「跳過 evidence-delta」的代價（少了 SKIP）。
```

主打對照圖（若做）：B2 vs 我們 —— 同一句被改寫但 triple 不變時，我們 SKIP、B2 仍失效。

## 14. 指標（只取 log 免費送的）

不另寫評估 harness，直接從 `evidence_deltas` / `refresh_decisions` 統計：

```text
每個 edit step：
  added / removed / unchanged evidence 筆數
  SKIP / PATCH / REBUILD 決策數
  名目 cost 總和，對照「全部 REBUILD」的 cost（= 我們省下多少）
  被標 stale 的物件數（漸進維護模式下，跨 step 的 stale 曲線）
```

Demo 關鍵畫面：一次 edit 的 before/after KG 圖 + 該 step 的 delta/decision log。

## 15. 最小可行範圍

### 15.1 必須實作

- DocRED/Re-DocRED loader（建 documents / sentences / entities / relations / mentions / sentence_supports_relation）。
- Synthetic edit loader（含 `intended_triples`，選項 A）。
- 8 個 collection + §9 索引。
- §10 同步更新主幹：edit -> affected evidence -> M1 delta -> M2 decision -> freshness。
- §11 refresh 執行（immediate 必做；deferred 漸進模式建議做，給 demo 用）。
- 從 log 出 §14 的數字。

### 15.2 可簡化

- `T0` 的 entity / relation / evidence 直接用 DocRED `vertexSet` / `labels` / `labels.evidence` annotation，不跑抽取。
- Edited sentence 的新 evidence 用選項 (A) authored triples。
- Entity resolution 用 `norm_name` 比對。
- REBUILD 的「重解析」可簡化為「依現有 evidence 重設 aggregate / 刪空 edge」。

### 15.3 第一版不做

- chunks / extraction windows（已收斂到 sentence）。
- community / summary / embedding。
- 背景 worker / lease / retry / recovery。
- bitemporal 多版本。
- production NER / entity resolution、完整 QA、vector index、ArangoDB 原始碼修改。
- (B) LLM extractor（有空再加，介面同 A）。

## 16. 實作里程碑

```text
M1  Schema        : 建 8 個 collection 與 §9 索引。
M2  Ingestion     : DocRED loader -> sentences / entities / relations
                    / mentions / sentence_supports_relation（T0 圖）。
M3  Edit + M1     : synthetic edit application、改 sentence、找 affected evidence、
                    算 evidence_deltas、同步更新 provenance edge。
M4  M2 + Freshness: decision table -> refresh_decisions、標 stale。
M5  Refresh       : apply_refreshes（immediate + deferred 漸進模式）。
M6  Demo（可選）  : before/after 圖、log 數字、（可選）B0/B1/B2 對照。
```

## 17. 風險與緩解

```text
風險：看起來太像應用層
  緩解：所有 evidence delta / decision / freshness 都持久化在 ArangoDB；
        edit step 用 transaction；affected set 用 provenance edge 的 1-hop traversal；
        框架化為 evidence-aware IVM 的邏輯資料庫支援。

風險：evidence extraction 變成主要工作
  緩解：選項 (A) authored triples + DocRED annotation，extraction 不是重點。

風險：deferred refresh 破壞 pipeline
  緩解：§11.3 的 invariant —— 脊椎永遠同步、aggregate 修復才可延後、
        apply 為 idempotent、stale 可見。先把 immediate 模式做穩再開 deferred。

風險：entity resolution 連鎖（真正的「ripple」）
  緩解：用 norm_name 保持 deterministic；demo 的 edit 設計成可控，
        除非那條連鎖正是要展示的畫面。
```

## 18. 最終框架

建議標題：

> **Evidence-Aware Incremental GraphRAG Maintenance on ArangoDB**

一句話貢獻：

> 純 ArangoDB 的增量 KG 維護系統，持久化 evidence delta、invalidation decision 與 freshness 狀態，使 corpus 改動只沿 provenance 重算「evidence 真正改變」的 KG 物件，相較 generic traversal 與 naive invalidation 減少不必要的 recomputation。

## 19. 結論

純 ArangoDB + sentence 粒度 + 同步主幹 + 可選漸進 refresh，是課程時程內最務實的實作路徑。把 §10 的脊椎做穩、§11 的 invariant 守住，pipeline 就成立；其餘（deferred 漸進、baseline 對照、embedding 增強）都是錦上添花。
