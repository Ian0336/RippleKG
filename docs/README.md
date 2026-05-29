# RippleKG — 文件導覽

> **RippleKG**：面向 LLM 生成知識圖的增量檢視維護（Incremental View Maintenance）。
> Corpus 更新時，沿著 evidence 漣漪傳播，物化進 ArangoDB 的 provenance、delta、freshness 狀態，讓 KG 維護從 *full rebuild* 變成 *surgical refresh*。

---

## 三份文件

| 檔案 | 角色 | 抽象層 | 對誰寫 |
|---|---|---|---|
| `proposal.md` | **主提案**：scope、contribution、機制總覽、baselines、評估、分工 | What & Why | 教授 / TA / 第一次接觸專案的人 |
| `thought.md` | **M1/M2 實作補充（精簡版）**：ArangoDB schema、freshness 物件模型、provenance、index、update path、refresh 執行 | How (design) | 組員開工前必讀，特別是 B（DB owner）與 C（mechanism lead） |
| `implementation.md` | **實作筆記**：code-level 決策、模組介面、實際 query、跑出來的數字 | How (code) | 跟著 codebase 一起長的 living doc |

---

## 三份文件的關係

```
                   ┌──────────────────┐
                   │   proposal.md    │   高層提案
                   │   §0–§9          │   What / Why / 對誰報告
                   └────────┬─────────┘
                            │ §3 機制 / §4 stack 引出
                            ▼
                   ┌──────────────────┐
                   │   thought.md     │   M1/M2 設計補充
                   │   §0–§19         │   Schema / Provenance / Update path
                   └────────┬─────────┘
                            │ §10 update path / §11 worker 引出
                            ▼
                   ┌──────────────────┐
                   │ implementation.md│   實際 code 與量測
                   │   (WIP)          │   模組 / AQL / 數字
                   └──────────────────┘
```

- **`proposal.md`** 是入口。它定義專案 scope（限定在 `corpus → KG` 路徑），講清楚為什麼這是 DBMS contribution，並把核心拆成 M1 / M2 兩個 mechanism。
- **`thought.md`** 把 proposal §3 提的 M1 / M2 放大成可實作的 schema 與 update path。**所有設計細節（collection 名稱、欄位設計、provenance traversal）的真理來源**。已精簡到 8 個 collection、sentence 粒度、同步主幹 + 可選漸進 refresh。
- **`implementation.md`** 是 thought.md 落地後的紀錄 — 真實長出來的 module / 真的 AQL query / 真的跑出來的數字。隨 code 一起更新。

**單向引用規則**：`implementation.md` 引用 `thought.md`；`thought.md` 引用 `proposal.md`。反向引用視為設計回流，要先更新上游。

---

## 推薦閱讀順序

| 我是… | 順序 |
|---|---|
| 教授 / TA | `proposal.md` 全文 |
| 第一次接觸專案的組員 | `proposal.md` 全文 → `thought.md` §0–§5 → `implementation.md` |
| **B（ArangoDB / Schema owner）** | `proposal.md` §4, §9 → `thought.md` §5–§9（schema + index）+ §10–§11（update path + refresh 執行） |
| **C（M1/M2 Mechanism Lead）** | `proposal.md` §3 → `thought.md` §10（update path）+ §7（evidence record）+ §14（metric）|
| **A（Baselines + Corpus）** | `proposal.md` §5, §6 → `thought.md` §13（baselines）|
| **D（Eval + Demo + Paper）** | `proposal.md` 全文 → `thought.md` §14（metric）+ §15（最小可行）|

---

## Scope 共識（三份都遵守）

必要路徑限定為 **persisted `corpus → KG`**：

```
corpus edit
  → changed sentence/span
  → affected evidence
  → semantic evidence delta
  → affected KG entity/edge
  → SKIP/PATCH/REBUILD decision
  → persisted KG/provenance/freshness state in ArangoDB
```

**Stretch goals**（時間有餘才做、不進核心 contribution）：community grouping、summary、embedding、QA、query-time lazy refresh。

---

## 文件狀態

| 檔案 | 狀態 | 最後更新 |
|---|---|---|
| `proposal.md` | ✅ 主體完成，scope 已 freeze | 2026-05-29 |
| `thought.md` | ✅ 精簡版設計完成，schema 已 freeze（8 collection，W1 結束前不再大改） | 2026-05-29 |
| `implementation.md` | 🚧 待 W1 開工後逐步填寫 | — |

---

## 修改紀律

- 改 `proposal.md` § scope / contribution / stack：**需四人共識**。
- 改 `thought.md` schema / update path：**B 跟 C 共同 review**，因為動到他們的核心。
- 改 `implementation.md`：**模組 owner 自由更新**，但 PR 進主分支時要附對應段落 link。
