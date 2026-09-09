# local_setup

`AI_PROVIDER=local` 模式的本地向量索引建置腳本。**手動執行**,不隨 `docker compose up` 自動跑(索引建置耗時,且應由人決定何時重建)。

## 前置條件

1. `docker/docker-compose.yml` 已啟動 `postgres` 服務(image `pgvector/pgvector:pg16`,對外 port `5432`,user/pass/db = `appeal/appeal/appeal`)。
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

1. 對 Postgres 執行契約 DDL(`CREATE EXTENSION IF NOT EXISTS vector` + 四張表 `IF NOT EXISTS`),對全新 DB 也能獨立跑。
2. 讀 `data/output/law_chunks.jsonl` + `interp_chunks.jsonl`,以 32 筆為一批呼叫 ollama `/api/embed` 取得向量,寫入 `law_chunks` 表。
3. 讀 `data/output/case_chunks.jsonl`,同樣方式寫入 `case_chunks` 表。
4. 讀 `data/output/law_chunks.jsonl`(不需 embedding),整份寫入 `law_articles` 表(法條精查表,取代 DynamoDB;key 為 `法名#條號`)。
5. 印出各表最終筆數(`SELECT count(*)`)。

## 環境變數(皆可覆寫預設值)

| 變數 | 預設值 | 說明 |
|---|---|---|
| `LOCAL_LLM_BASE_URL` | `http://localhost:11434` | ollama base URL(主機直連;容器內另用 `host.docker.internal`,與本腳本無關) |
| `LOCAL_EMBED_MODEL` | `bge-m3` | embedding 模型名稱 |
| `POSTGRES_URL` | `postgresql://appeal:appeal@localhost:5432/appeal` | Postgres 連線字串(主機直連 compose 對外 port) |

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
開始匯入 law_articles(law_chunks.jsonl): 2214 筆
  [PROGRESS] law_articles: 2214/2214
[DONE] law_chunks=2362 case_chunks=233 law_articles=2214
```

## 重跑說明

所有寫入皆為 `ON CONFLICT ... DO UPDATE`(law_chunks/case_chunks 以 `id` 為衝突鍵,law_articles 以 `law_article` 為衝突鍵),因此可重複執行整支腳本以更新資料(例如前處理重新產出 JSONL 後),不會產生重複列,也不需要先清空資料表。

## 失敗處理

腳本不含重試邏輯:ollama 呼叫失敗或 Postgres 錯誤會直接印出錯誤訊息並以非 0 狀態碼結束,需排除問題後重新執行整支腳本(已寫入的批次因 upsert 語意不會重複,可安全重跑)。
