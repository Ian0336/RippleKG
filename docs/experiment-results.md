# RippleKG 完整實驗結果

本文件整理 RippleKG 目前已完成的實驗設計、量化結果、錯誤分析與可重現指令。

實驗目標不是宣稱系統已達到通用生產環境準確率，而是驗證以下端到端假設：

```text
更新事實
  -> 找出可能受影響的句子
  -> 過濾真正需要修改的句子
  -> 產生 EditOp
  -> M1 計算 evidence delta
  -> M2 做出 SKIP / PATCH / REBUILD 決策
```

## 1. 實驗環境

| 項目 | 設定 |
|---|---|
| Database | ArangoDB 3.12 |
| 初始資料 | Re-DocRED `dev_revised.json` |
| 已載入文件 | 5 篇 |
| 已載入句子 | 41 句 |
| Embedding backend | `sentence-transformers:all-MiniLM-L6-v2` |
| LLM provider | Anthropic |
| Candidate scope | 文件內 sentence retrieval |
| End-to-end 評估資料庫 | 獨立 `ripplekg_eval`，不修改主資料庫 |

目前共有兩種 Gold Label：

1. **Re-DocRED evidence gold**：資料集標記為支持 relation fact 的句子，用於評估 evidence retrieval。
2. **Reviewed should-edit gold**：判斷舊 fact 被新 fact 明確取代後，句子是否真的需要修改，用於評估候選選句、LLM gate 與端到端更新。

這兩者不可混為一談。Evidence sentence 可能只提供間接推論，不一定需要因 fact replacement 而修改。

## 指標與專案名詞定義

### 專案資料與流程名詞

| 名詞 | 本專案中的意思 |
|---|---|
| KG | Knowledge Graph，使用 entity 與 typed relation 表示知識 |
| Re-DocRED | 用來建立初始 T0 KG 與 provenance 的 document-level relation extraction dataset |
| LLM | Large Language Model；本專案用於 relevance 判斷、句子改寫與 triple extraction |
| fact / triple | 一筆 `(head entity, relation, tail entity)` 敘述 |
| evidence sentence | 被資料集或 provenance edge 標記為支持某個 KG object 的句子 |
| provenance | 記錄句子支持哪些 entity / relation 的來源連結 |
| evidence count | 目前仍 active、支持該 KG object 的 provenance edge 數量 |
| one-hop traversal | 從 changed sentence 沿一條 provenance edge 找直接受影響的 entity / relation |
| candidate sentence | 搜尋階段找出的候選句，尚不代表一定要修改 |
| T0 | 尚未套用 incremental edit 前的初始 corpus、KG 與 provenance 狀態 |
| incremental refresh | 只更新受影響的 graph state，而不是重建整張 KG |
| `EditOp` | 統一的修改資料結構：`doc_id`, `sent_idx`, `new_text`, `intended_triples` |
| `intended_triples` | 編輯後該句應支持的所有 triples，不是整篇文件的完整 KG |
| M1 | 本專案自訂名稱：計算 evidence 的 `added / removed / unchanged` |
| M2 | 本專案自訂名稱：根據 M1 delta 決定 `SKIP / PATCH / REBUILD` |
| `SKIP` | Evidence 未改變，不更新該 KG object |
| `PATCH` | 增量更新該 object 的 evidence-derived state，不是 HTTP PATCH |
| `REBUILD` | 重建或移除單一受影響 KG object，不是重建整張 KG |
| freshness | KG object 是已更新的 `fresh`，或等待 refresh 的 `stale` |
| relevance gate | 判斷 candidate sentence 是否真的需要修改的分類器 |
| embedding | 將句子轉換成數值向量，以進行相似度搜尋 |
| cosine similarity | 用於向量排序的相似度分數；越高通常越相似，但不是正確機率 |
| schema merge | 將 LLM 產生的 relation wording 對應至 KG canonical relation |
| relation-aware verifier | M1 前的 deterministic guardrail；驗證 triples 並處理明確 replacement |
| synthetic replacement | 為了可重現評估而產生的 controlled old-fact-to-new-fact 更新 |
| AQL | ArangoDB Query Language |
| benchmark | 固定的評估案例集合，用於量測系統表現 |
| baseline | 用來和 RippleKG 比較的較簡單方法 |

### 評估指標

| 指標 | 意思 |
|---|---|
| Gold Label | 評估時預先定義的正確答案 |
| Top-K | 相似度排序後的前 K 個結果 |
| Hit@K | 每個查詢的 Top-K 中是否至少命中一個 Gold answer，再對所有查詢取平均 |
| MRR | Mean Reciprocal Rank；每個查詢取第一個正確答案排名的倒數，再計算平均 |
| TP | True Positive；系統選中，而且人工 Gold 也認為應選 |
| FP | False Positive；系統選中，但人工 Gold 認為不應選 |
| FN | False Negative；系統沒選中，但人工 Gold 認為應選 |
| TN | True Negative；系統沒選中，人工 Gold 也認為不應選 |
| Precision | `TP / (TP + FP)`；系統選出的結果有多少是真的正確 |
| Recall | `TP / (TP + FN)`；所有正確答案中有多少被系統找回 |
| F1 | Precision 與 Recall 的調和平均，用來衡量兩者平衡 |
| Accuracy | `(TP + TN) / 全部案例`；所有正負案例中判斷正確的比例 |
| similarity threshold | 接受候選的最低 cosine similarity；不是正確機率或百分比 |
| EditOp content accuracy | 真正應修改的句子中，同時完成文字變更、移除舊 triple、加入新 triple 的比例 |
| M1 added/removed relation accuracy | 真正應修改的操作中，M1 是否同時偵測到指定舊 relation removed 與新 relation added |
| end-to-end M2 accuracy | 從 EditOp、M1 到 M2，最終是否產生預期決策 |
| M2 policy-rule accuracy | 只檢查 M2 對實際收到的 delta 是否依決策表做對 |

MRR 範例：若第一個正確答案排名為第 1、2、4 名，reciprocal rank 分別是 `1`、`1/2`、`1/4`，MRR 是三者平均。

## 2. 實驗資料

### 2.1 Evidence retrieval benchmark

從目前 ArangoDB 中的 relation 與 `sentence_supports_relation` provenance edge 建立：

| 項目 | 數量 |
|---|---:|
| 測試 facts | 50 |
| Gold evidence sentences | 66 |
| 每次搜尋上限 | Top-10 |

每個查詢由 KG triple 轉成：

```text
<head> <relation> <tail>.
```

### 2.2 Should-edit benchmark

建立明確的 `replace_old_fact` 更新案例：

| 項目 | 數量 |
|---|---:|
| Fact replacement cases | 30 |
| Candidate sentences | 150 |
| Reviewed should-edit = true | 20 |
| Reviewed should-edit = false | 130 |

標記規則：

> 只有當句子本身陳述或結構性表達舊 fact，且舊 fact 被新 fact 明確取代後句子會過時，才標記為需要修改。

目前標籤狀態為 `assistant_reviewed_pending_owner_spot_check`。正式報告前應由專案成員抽查。

## 3. Transformer Evidence Retrieval

### 3.1 Ranking metrics

`Hit@K` 用來回答：

> 對每個輸入 fact，搜尋結果前 K 名中，是否至少出現一個正確的 Gold evidence sentence？

每個 fact 若在前 K 名中至少命中一次，該 fact 的 Hit 計為 1；否則計為 0。最後對所有 facts 取平均。

例如正確句子是 `sentence 5`：

```text
搜尋排名：
1. sentence 2  錯誤
2. sentence 5  正確
3. sentence 4  錯誤
```

這筆查詢的結果為：

```text
Hit@1 = 0    # 第一名不是正確句子
Hit@3 = 1    # 前三名中包含正確句子
```

`Hit@3 = 1` 不代表前三句全部正確；它只代表前三名中至少命中一個正確答案。因此 Hit@K 適合評估候選搜尋是否漏掉答案，但不能單獨衡量 false positives。

| 指標 | 結果 |
|---|---:|
| Hit@1 | 86.0%（43/50） |
| Hit@3 | 100.0%（50/50） |
| Hit@5 | 100.0%（50/50） |
| Hit@10 | 100.0%（50/50） |
| MRR | 0.930 |

解讀：

- 50 個 fact 中，有 43 個第一名就是正確 evidence。
- 所有 fact 都至少有一個正確 evidence sentence 進入前三名。
- `Hit@3 = 100%` 不代表前三名全部正確，也不代表所有 gold evidence 都被找回。

### 3.2 Similarity threshold sweep

| Threshold | Precision | Recall | F1 | FP | FN |
|---:|---:|---:|---:|---:|---:|
| 0.30 | 42.2% | 86.4% | 56.7% | 78 | 9 |
| 0.40 | 58.4% | 78.8% | **67.1%** | 37 | 14 |
| 0.50 | **69.0%** | 60.6% | 64.5% | 18 | 26 |
| 0.60 | 68.0% | 25.8% | 37.4% | 8 | 49 |
| 0.65 | 58.3% | 10.6% | 17.9% | 5 | 59 |
| 0.70 | 83.3% | 7.6% | 13.9% | 1 | 61 |
| 0.75 | 100.0% | 1.5% | 3.0% | 0 | 65 |

結論：

- 固定高 threshold 會漏掉大量 evidence。
- `0.40` 在這組 evidence gold 上有最高 F1。
- 若後續有 LLM relevance gate，可使用較低 threshold 或 Top-K，優先維持 recall。

## 4. 真正需要修改句子的 Retrieval

使用 reviewed should-edit gold，而不是 Re-DocRED evidence gold。

| 選句方法 | Precision | Recall | F1 | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|
| Graph provenance suggestion | **58.8%** | **100.0%** | **74.1%** | 20 | 14 | 0 |
| Embedding Top-1 | 50.0% | 75.0% | 60.0% | 15 | 15 | 5 |
| Embedding Top-3 | 21.1% | 95.0% | 34.5% | 19 | 71 | 1 |
| Embedding Top-5 | 13.3% | 100.0% | 23.5% | 20 | 130 | 0 |
| Threshold 0.30 | 25.0% | 80.0% | 38.1% | 16 | 48 | 4 |
| Threshold 0.40 | 28.2% | 55.0% | 37.3% | 11 | 28 | 9 |
| Threshold 0.50 | 44.4% | 40.0% | 42.1% | 8 | 10 | 12 |

結論：

- Graph provenance 沒有漏掉應修改句，但包含 14 個 false positives。
- Embedding Top-3 具有高 recall，但 false positives 很多。
- Embedding 適合產生候選，不適合單獨決定是否修改。
- Graph provenance 與 embedding 後仍需要 relevance gate。

## 5. LLM Relevance Gate

Anthropic relevance gate 對 30 個案例、150 個候選句進行判斷：

| 指標 | 結果 |
|---|---:|
| Precision | 76.9% |
| Recall | 100.0% |
| F1 | 87.0% |
| Accuracy | 96.0% |
| TP | 20 |
| FP | 6 |
| FN | 0 |
| TN | 124 |

相比 Graph provenance suggestion：

| 方法 | Precision | Recall | F1 |
|---|---:|---:|---:|
| Graph provenance | 58.8% | 100.0% | 74.1% |
| Anthropic relevance gate | **76.9%** | **100.0%** | **87.0%** |

LLM gate 保留所有真正需要修改的句子，同時將 Precision 從 58.8% 提升至 76.9%。

主要 false positives 來自間接地理推論，例如：

```text
Fact:
Latin American School of Medicine country Cuba.

Sentence:
ELAM was operated by the Cuban government.
```

LLM 認為該句也應修改，但人工規則要求句子需直接陳述舊 fact，因此標記為不需要修改。

## 6. Final EditOp Generation

LLM gate 共選出 26 個候選句：

| 項目 | 結果 |
|---|---:|
| Gate-selected candidates | 26 |
| 成功生成 EditOp | 25 |
| Generation failed or rejected | 1 |
| Gate false positives 中真正生成錯誤 EditOp | 5 |

針對 20 個真正需要修改的句子：

| 指標 | 結果 |
|---|---:|
| EditOp content correct | 19/20 |
| EditOp content accuracy | **95.0%** |

唯一 content failure：

```text
Vancouver country Canada
-> Vancouver country Canadian
```

這是一個語意不自然的 synthetic replacement。LLM 更新了 intended triple，但沒有修改句子文字，因此 content 評估判定失敗。

## 7. M1 / M2 End-to-End Results

### 7.1 修正前

最初的 `verify_supported_old_triples()` 只檢查舊 triple 的 head 與 tail 是否仍出現在新句子中。

若舊 tail 在句子其他片段仍出現，系統會錯誤保留已被取代的舊 triple。

例如：

```text
Old fact:
Guri country Venezuela

New fact:
Guri country Canada

Edited sentence still contains:
Venezuela government
```

舊 verifier 因為仍看到 `Guri` 與 `Venezuela`，錯誤保留：

```text
Guri country Venezuela
```

修正前結果：

| 指標 | 結果 |
|---|---:|
| EditOp content accuracy | 80.0% |
| M1 added/removed relation accuracy | 80.0% |
| End-to-end M2 accuracy | 80.0% |
| M2 policy-rule accuracy | 100.0% |

### 7.2 Replacement-aware verifier 改進

新版 verifier：

- 從明確 `replace old fact with new fact` 指令識別真正被取代的舊 triple。
- 若 candidate 包含相同 head/relation、不同 tail 的 replacement，不再補回指定舊 triple。
- 只 supersede 指令明確指定的 triple。
- 保留多值 relation 中未被指定取代的其他 values。

### 7.3 修正後

| 指標 | 修正前 | 修正後 |
|---|---:|---:|
| EditOp content accuracy | 80.0% | **95.0%** |
| M1 added/removed relation accuracy | 80.0% | **100.0%** |
| End-to-end M2 accuracy | 80.0% | **100.0%** |
| M2 policy-rule accuracy | 100.0% | **100.0%** |

修正後，20 個真正需要修改的操作中：

```text
M1 correct: 20/20
M2 end-to-end correct: 20/20
M2 policy-rule correct: 25/25 generated operations
```

這說明原本的端到端錯誤來源不是 M2 policy，而是 M1 前的 intended-triple verifier。

## 8. 主要結論

1. **Transformer retrieval 適合找候選，不適合單獨決定修改。**  
   Hit@3 達 100%，但選取所有 should-edit sentences 時 Precision 偏低。

2. **Graph provenance 是高 recall 的 affected-set 起點。**  
   在 reviewed should-edit benchmark 中 Recall 為 100%，但仍會包含間接 evidence。

3. **LLM relevance gate 能提升 precision 且維持 recall。**  
   Precision 從 provenance 的 58.8% 提升至 76.9%，Recall 維持 100%。

4. **M2 policy 本身是 deterministic 且穩定的。**  
   對實際收到的 delta，M2 policy-rule accuracy 為 100%。

5. **M1 輸入品質決定端到端結果。**  
   Replacement-aware verifier 將 M1 與 end-to-end M2 準確率從 80% 提升至 100%。

6. **建議的完整流程：**

```text
Graph provenance + Transformer retrieval
  -> LLM relevance gate
  -> LLM EditOp generation
  -> relation-aware replacement verifier
  -> M1 evidence delta
  -> M2 SKIP / PATCH / REBUILD
```

## 9. 限制

- Should-edit benchmark 只有 30 個 synthetic replacement cases。
- 實驗目前只載入 5 篇 Re-DocRED 文件，不代表 500 篇或其他資料集上的效果。
- Reviewed labels 目前由 AI 協助標記，正式報告前應由組員人工抽查。
- Re-DocRED evidence gold 不一定等同於真正需要修改的句子。
- 部分 synthetic replacements 語意不自然，例如將 country 從 `Canada` 改成 `Canadian`。
- LLM provider 可能逾時、缺少欄位或拒絕產生 EditOp；評估工具會快取、重試並記錄失敗。
- Native ArangoDB vector index 尚需 server-side vector-index 支援；否則使用 exact Python cosine scan。

## 10. 可重現指令

### Evidence retrieval

```bash
docker compose exec api python scripts/evaluate_relation_retrieval.py \
  --cases 50 \
  --limit 10 \
  --scope document \
  --output data/relation_retrieval_eval.json
```

### Reviewed should-edit retrieval

```bash
python scripts/evaluate_edit_annotations.py data/edit_annotation_set.json
```

### LLM relevance gate

```bash
docker compose exec api python scripts/evaluate_llm_relevance_gate.py \
  --provider anthropic \
  --output data/llm_relevance_gate_eval.json
```

### Final EditOp and M1/M2

```bash
docker compose exec api python scripts/evaluate_end_to_end_edits.py \
  --provider anthropic \
  --output data/end_to_end_edit_eval.json
```

### Focused tests

```bash
pytest -q tests/test_extraction.py tests/test_pipeline_m1_m2.py tests/test_embeddings.py
```

目前 focused verification 結果：

```text
14 passed, 3 skipped
```

## 11. 實驗輸出檔案

| 檔案 | 用途 |
|---|---|
| `data/relation_retrieval_eval.json` | 50-fact Transformer evidence retrieval 結果 |
| `data/edit_annotation_set.json` | 30 個 replacement cases 與 150 個 reviewed candidates |
| `data/llm_relevance_gate_eval.json` | LLM relevance gate 決策、理由與 metrics |
| `data/end_to_end_edit_eval.json` | 最終 EditOps、M1 deltas、M2 decisions 與 summary |
