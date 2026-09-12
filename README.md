# AI Price Index

每日自動收集全球 LLM API 價格，永久保存，發布成公開資料庫同價格變動日誌。

**零成本、零人手、每日自動運行。**

- 📋 **[SETUP.md](SETUP.md)** — 一次性設定（20 分鐘，只有你做得到嘅步驟）
- 🧭 **[STRATEGY.md](STRATEGY.md)** — 點解揀呢條路，同埋錢幾時會入嚟（誠實版）

---

## 而家有乜

| | |
|---|---|
| 追蹤模型 | 445 個（440 個有公開價格） |
| 供應商 | 59 個 |
| 生成頁面 | 44（40 個模型頁，隨數據增長） |
| 數據源 | OpenRouter 公開 API + 5 個廠商定價頁指紋 |
| 依賴 | Python 標準庫 + `certifi`。冇 npm，冇框架 |
| 運行成本 | HK$0 |

---

## 本機跑

```bash
python -m pip install certifi
python engine/collect.py      # 收集今日數據（約 30 秒）
python engine/build_site.py   # 生成靜態站到 site/
```

開 `site/index.html` 睇成果。

> ⚠️ **每日機械人會 commit 數據上 GitHub**，所以你本機改嘢之前記得先：
>
> ```bash
> git pull --rebase origin main
> ```
>
> 唔 pull 就 push 會被 reject（`non-fast-forward`）。

---

## 結構

```
engine/collect.py      每日收集器。攞價格、存快照、同前一日 diff 出變動事件
engine/build_site.py   靜態站生成器。讀晒所有快照，砌出索引、變動日誌、模型頁
config/models.json     廠商登記表（次要數據源）
config/site.json       網址同 base path ← 部署前要改
data/snapshots/*.json  每日一個快照。呢個就係資產本身
data/changes.jsonl     累積嘅變動事件流。呢個就係產品
site/                  生成物（可以隨時刪，重新 build 就返嚟）
.github/workflows/     每日自動化
```

---

## 設計上嘅硬規矩

呢幾條寫死咗喺 code 入面，改之前請先睇 [STRATEGY.md](STRATEGY.md) 第五節：

1. **頁面要「賺」到被生成嘅資格** — 唔會一次過噴幾萬頁（`HISTORY_GATE`）
2. **缺數據顯示 `—`，永遠唔會當 0** — 假數據會摧毀成個站唯一嘅賣點
3. **主數據源攞唔到就硬失敗** — 一個講大話嘅快照會污染之後所有 diff
4. **歷史快照永不修改** — 向前修正，保持紀錄誠實
5. **尊重 robots.txt 同拒絕爬蟲嘅廠商** — 唔繞過

---

## 最重要嘅一件事

**每日收集，永遠唔好停。**

呢個資產嘅護城河係時間，唔係聰明。今日嘅價格人人查得到；「2026 年 9 月 12 日嗰日邊個模型幾錢」只有由嗰日開始收集嘅人先有。斷咗嘅日子係補唔返嘅。
