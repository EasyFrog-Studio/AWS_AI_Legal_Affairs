# local_setup

`AI_PROVIDER=local` 模式的本地向量索引建置腳本。**手動執行**,不隨 `docker compose up` 自動跑(索引建置耗時,且應由人決定何時重建)。

## 前置條件

1. `docker/docker-compose.yml` 已啟動 `postgres` 服務(image `pgvector/pgvector:pg16`,對外 port `5433`(容器內仍是 5432;主機常見已有原生 PostgreSQL 佔用 5432),user/pass/db = `appeal/appeal/appeal`)。
2. 主機已安裝 ollama,且已 `ollama pull bge-m3`(1024 維 embedding 模型)。
3. Python 環境已安裝依賴:
   ```
   pip install "psycopg[binary]" httpx
   ```

## 執行

工作目錄需為 `AWS_dev_infomation/AWS_AI_Legal_Affairs/local_setup/`:

```
cd AWS_dev_infomation/AWS_AI_Legal_Affairs/local_setup
python ingest.py
```

腳本會依序:

1. 對 Postgres 執行契約 DDL(`CREATE EXTENSION IF NOT EXISTS vector` + 五張表 `IF NOT EXISTS`),對全新 DB 也能獨立跑。
2. 讀 `data/output/law_chunks.jsonl` + `interp_chunks.jsonl`,以 32 筆為一批呼叫 ollama `/api/embed` 取得向量,寫入 `law_chunks` 表。
3. 讀 `data/output/case_chunks.jsonl`,同樣方式寫入 `case_chunks` 表。
4. 讀 `data/output/answer_chunks.jsonl`,同樣方式寫入 `answer_chunks` 表(訴願答辯書;`source_kind="測資"` 的列是合成卷證,取用前要自行排除)。
5. 讀 `data/output/law_chunks.jsonl`(不需 embedding),整份寫入 `law_articles` 表(法條精查表,取代 DynamoDB;key 為 `法名#條號`)。
6. 印出各表最終筆數(`SELECT count(*)`)。

## 環境變數(皆可覆寫預設值)

| 變數 | 預設值 | 說明 |
|---|---|---|
| `LOCAL_LLM_BASE_URL` | `http://localhost:11434` | ollama base URL(主機直連;容器內另用 `host.docker.internal`,與本腳本無關) |
| `LOCAL_EMBED_MODEL` | `bge-m3` | embedding 模型名稱 |
| `POSTGRES_URL` | (無預設,必填) | Postgres 連線字串,主機直連 compose 對外 port:`postgresql://appeal:<POSTGRES_PASSWORD>@localhost:5433/appeal`,密碼取 `.env` 的 `POSTGRES_PASSWORD`;未設定即 `SystemExit` |

## 預期輸出(範例)

```
[DDL] 已確認 extension/tables 存在
開始匯入 law_chunks(law+interp): 2362 筆
  [PROGRESS] law_chunks: 32/2362
  ...
  [PROGRESS] law_chunks: 2362/2362
開始匯入 case_chunks: 233 筆
  [PROGRESS] case_chunks: 32/233
  ...
  [PROGRESS] case_chunks: 233/233
開始匯入 answer_chunks: 78 筆
  [PROGRESS] answer_chunks: 78/78
開始匯入 law_articles(law_chunks.jsonl): 2214 筆
  [PROGRESS] law_articles: 2214/2214
[DONE] law_chunks=2362 case_chunks=233 answer_chunks=78 law_articles=2214
```

## 重跑說明

所有寫入皆為 `ON CONFLICT ... DO UPDATE`(law_chunks/case_chunks/answer_chunks 以 `id` 為衝突鍵,law_articles 以 `law_article` 為衝突鍵),因此可重複執行整支腳本以更新資料(例如前處理重新產出 JSONL 後),不會產生重複列,也不需要先清空資料表。

## 失敗處理

腳本不含重試邏輯:ollama 呼叫失敗或 Postgres 錯誤會直接印出錯誤訊息並以非 0 狀態碼結束,需排除問題後重新執行整支腳本(已寫入的批次因 upsert 語意不會重複,可安全重跑)。

## 爬蟲語料(ingest_crawl.py)

`data/爬蟲集/chunk資料/` 是另一套已切好的語料(訴願決定書 49,570 chunk、法條 6,437 chunk),欄位名與 `data/output/` 不同(頂層 `片段名`/`內容`,metadata 用 `clause`/`article`/`revised_date`),故另有一支映射腳本:

```
cd local_setup
python ingest_crawl.py       # 灌入同樣的 law_chunks / case_chunks / law_articles
python -m pytest test_ingest_crawl.py -q   # 映射層測試
```

映射與過濾規則(全部在 `ingest_crawl.py`,有測試涵蓋):

| 規則 | 行為 |
|---|---|
| 留出年度 | `metadata.year` 為 114 的決定書 chunk 回 `None` 不入庫(與 `parse_decisions.py`、`ingest.py` 並列第三道防線) |
| 條款次 | `clause` `§77(2)` → `appeal_article` `77(2)`;`§79`、`§79+§81`、`未判定` 等無款次者填空字串,不造假鍵 |
| 案型 | 來源的 `case_type` 是自由文字案由(語料共 630 種),F3 的完全相等過濾對它必然落空。改用 `決定書-{案型}.jsonl` 的檔名分桶(11 種)當 `case_type`,原始案由保留為 `case_subtype`。送進 embedding 的 `text` 表頭仍用原始案由——已入庫向量是照原表頭算的,改表頭會讓重跑結果與現有向量不一致 |
| 爭點 | 爬蟲語料無爭點欄,`issue` 一律留空 —— F3 的第二層 filter 在這批資料上不生效 |
| 刪除條文 | `deleted == "true"` 回 `None`,不進 F2 推薦 |
| 官方法規 | `OFFICIAL_LAW_NAMES` 這 11 部的爬蟲版一律不收,避免同鍵覆寫已驗算過的條數與修正日期 |
| `law_type` | 民法 → 普通法、`_PROCEDURE_LAWS` → 程序法、其餘領域法規 → 實體法(不用「其他」,否則 F2 排除普通法的過濾會失去意義) |
| `amend_date` | `revised_date` `20251226` → `民國 114 年 12 月 26 日`;抽不出來填「未收錄」,不猜 |

參考資料(司法院釋字 / 行政函釋 / 行政法院裁判)另由 `preprocessing/parse_crawl_reference.py` 先把 PDF 切成 chunk,輸出到 `data/爬蟲集/chunk資料/參考資料/`,本腳本以 `reference_rows()` 原樣載入(已是本專案 schema,不需映射)灌進 `law_chunks`。這批 `article_no` 為空,**不進 `law_articles` 精查表**(該表鍵為 `法名#條號`)。

實際入庫量:決定書 36,177(排除 114 年 13,393)、法條 4,133(排除 2,304)、參考資料 11,274(釋字 3,810 + 函釋 905 + 裁判 6,559;官方重複的 29 份跳過)。
