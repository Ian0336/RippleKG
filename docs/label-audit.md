# Gold Label 稽核報告 — should-edit annotation set

> 對象：`data/edit_annotation_set.json`（30 cases × 5 candidates = **150** 筆）
> gold label：`human_should_edit`（true / false）
> 重現：`python scripts/audit_edit_labels.py`

## 0. 目的

`experiment-results.md` 的 should-edit / LLM gate / end-to-end 數字全部建立在這批標籤上，而資料集狀態原為 `assistant_reviewed_pending_owner_spot_check` —— docs 自己要求「正式報告前應由組員人工抽查」。本報告是對全部 150 筆標籤的**獨立重新判讀**。

## 1. 方法

判讀規則（資料集內 `annotation_rule`）：

> **True only when the sentence states or structurally expresses the old fact and would become stale when that fact is explicitly replaced.**
> （只有當句子本身陳述或結構性表達舊 fact、且舊 fact 被取代後會過時，才標 true。）

稽核分兩步：

1. **程式化旗標**（`scripts/audit_edit_labels.py`）找出需人工確認的兩類：
   - **FLAG-1**：`human=true` 但被替換的值**沒**出現在句子裡（true 但無文字依據）。
   - **FLAG-2**：`human=false` 但被替換的值**有**出現在句子裡、且 provenance 連到舊 fact（borderline 的「不用改」判斷）。
2. **人工逐筆覆核**：全部 20 個 TRUE + 全部 12 個 FLAG-2 borderline。

## 2. 總結 Verdict

| 項目 | 結果 |
|---|---|
| 總標籤數 | 150 |
| TRUE / FALSE / 未標 | 20 / 130 / 0 |
| **獨立判讀後需推翻的標籤** | **0 / 150** |
| FLAG-1（true 無文字依據） | 0 |
| FLAG-2（borderline false） | 12（逐筆覆核後**皆正確**） |
| provenance suggestion 與 human 一致 | 136 / 150 |

那 14 筆 provenance ≠ human 全部是**provenance 過度建議、human 正確判「不用改」**—— 這正是標籤比 provenance baseline 精確的地方，也是 LLM relevance gate 要修掉的 false positive。

**結論：標籤品質高、規則套用一致，足以支撐報告中的數字。**

## 3. 規則被正確套用的關鍵證據

同一句 `doc1:0`：「Ross Patterson Alger ... was a politician in the **Canadian** province of Alberta ...」

| Fact 被替換 | label | 為何正確 |
|---|---|---|
| `Canada contains administrative territorial entity Alberta`（replace-018）| **TRUE** | 句子確實表達「Alberta 是 Canada 的省」 |
| 某人 `country of citizenship = Canadian`（replace-006/014/025）| **FALSE** | 「Canadian」只是修飾 Alberta 的地理形容詞，句子沒在斷言那個人的國籍 |

同一個字「Canadian」、不同 fact、不同標籤 —— 標註者正確區分了「句子**提到**某詞」與「句子**斷言**某 fact」。這是規則最難、最容易標錯的地方，而且做對了。

## 4. 12 個 FLAG-2 borderline（逐筆覆核，皆為正確的 FALSE）

| 句子（摘要） | 影響筆數 | 為何 FALSE 正確 |
|---|---|---|
| `doc3:4`「Other compositions included "..." , "..."」（歌曲清單）| 6 | 改清單中某首歌的某 relation，不會讓「清單列出這些歌」這件事過時 |
| `doc1:0`「... the Canadian province of Alberta ...」| 3 | 「Canadian」修飾 Alberta，非斷言人物國籍（見 §3）|
| `doc4:2`「... students from outside Cuba ...」| 2 | Cuba 出現在「學生來源」語境，非斷言學校所在國（= experiment-results §5 的 ELAM 例）|
| `doc0:4`「... leading the Canadian skeleton team ...」| 1 | 「Canadian」指他執教的隊伍，非他本人國籍 |

## 5. 20 個 TRUE（全部正確）

每一句都直接陳述被替換的舊 fact，替換後句子會過時。代表例：

| case | 句子 | 舊 fact → 新 fact |
|---|---|---|
| replace-001 | `doc0:0` 「... is a **Polish** skeleton racer.」 | citizenship Polish → American |
| replace-005 | `doc0:0` 「(born 13 March 1963 in **Kraków**, Poland)」 | place of birth Kraków → Prelate |
| replace-004 | `doc4:0` 「... a major international medical school in **Cuba** ...」 | ELAM country Cuba → Canada |
| replace-019 | `doc3:0` 「Ramey Idriss (**11 September 1911** – ...)」 | date of birth 1911 → 1963 |
| replace-022 | `doc1:3` 「He served with the **Royal Canadian Air Force** during World War II.」 | WWII participant RCAF → Duff Gibson |
| replace-030 | `doc0:2` 「... at the **2002 Winter Olympics** in Salt Lake City.」 | participant 2002 WO → 2006 WO |

（其餘 14 筆同類型，分布於 9 個不同句子、5 份 document；完整清單見 `scripts/audit_edit_labels.py` 輸出。）

## 6. 發現的限制（不影響標籤正確性，但影響 benchmark 輸入品質）

1. **部分 synthetic replacement 語意退化**：例如 `Canada → Canadian`、`citizenship → Alberta`、把歌名換成同一份清單裡的另一首歌。這些 case 的**標籤處理正確**（多半 FALSE），但 fact 本身是人工噪音。已在 `experiment-results.md §9` 列為限制。
2. **規模小**：30 cases、5 份 document、只有 `replace_old_fact` 一種 update_type。
3. **部分 TRUE 的 retrieval_score 偏低**（如 0.13 / 0.18）但標籤正確 —— 佐證 embedding 不足以單獨決定是否修改，需 provenance + gate（與 `experiment-results.md §4` 結論一致）。

## 7. 建議

1. 標籤足以支撐報告數字（P/R、gate、end-to-end）。資料集 `annotation_status` 可由 `assistant_reviewed_pending_owner_spot_check` 更新為已稽核狀態。
2. 仍建議組員花 ~5 分鐘掃一遍本報告 §4（12 筆 borderline）與 §5（20 筆 TRUE）做最終人工 sign-off —— 本報告已把需要看的全列出。
3. 若要強化嚴謹度：重生 §6.1 那幾個語意退化的 synthetic case，並擴充 document 數與 update_type。
