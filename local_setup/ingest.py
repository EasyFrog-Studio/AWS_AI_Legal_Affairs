"""讀三個 chunk JSONL,經 ollama embedding 灌入本地 Postgres(pgvector)。
手動執行(不隨容器啟動):law+interp -> law_chunks 表,case -> case_chunks 表,
answer -> answer_chunks 表,law_chunks.jsonl 另整份寫入 law_articles 表(取代 DynamoDB 法條精查表)。
可重跑:所有寫入皆 ON CONFLICT DO UPDATE。

依賴: pip install psycopg[binary] httpx
"""
import json
import os
import sys
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv

# --- 路徑推導(鏡像 AWS_dev_infomation/AWS_AI_Legal_Affairs/preprocessing/common.py:9-10 的相對寫法) ---
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "output"
LAW_CHUNKS_PATH = OUTPUT_DIR / "law_chunks.jsonl"
INTERP_CHUNKS_PATH = OUTPUT_DIR / "interp_chunks.jsonl"
CASE_CHUNKS_PATH = OUTPUT_DIR / "case_chunks.jsonl"
ANSWER_CHUNKS_PATH = OUTPUT_DIR / "answer_chunks.jsonl"

# --- 設定(環境變數可覆寫) ---
# .env 只有 compose 會自動注入,建索引是在主機上跑的,要自己載;既有環境變數優先
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

LOCAL_LLM_BASE_URL = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434")
LOCAL_EMBED_MODEL = os.environ.get("LOCAL_EMBED_MODEL", "bge-m3")
# 與 backend/app/config.py 的 LOCAL_LLM_TIMEOUT 同一個旋鈕:建索引是數千筆的長批次,
# 逾時值調小會讓整批在中途斷在一筆正常的慢 embedding 上
LOCAL_LLM_TIMEOUT = float(os.environ.get("LOCAL_LLM_TIMEOUT", "300"))
POSTGRES_URL = os.environ.get("POSTGRES_URL", "")  # 空字串=未設定,main() 連線前檢查,import 時不 raise

EMBED_BATCH_SIZE = 32

# 留出法測試集年度:這些年度的決定書 chunk 不得進檢索庫,與 preprocessing/parse_decisions.py 形成兩道防線
HOLDOUT_YEARS = frozenset({"114"})

# --- DDL(與 docker/initdb/01_schema.sql 一字不差) ---
DDL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS law_chunks (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding vector(1024)
);
CREATE TABLE IF NOT EXISTS case_chunks (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding vector(1024)
);
CREATE TABLE IF NOT EXISTS answer_chunks (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding vector(1024)
);
CREATE TABLE IF NOT EXISTS law_articles (
  law_article TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS appeal_cases (
  case_id TEXT PRIMARY KEY,
  data JSONB NOT NULL
);
"""


def load_jsonl(path: Path) -> list:
    if not path.exists():
        print(f"[SKIP] 找不到 {path}")
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def drop_holdout_years(rows: list) -> list:
    """濾掉 metadata.year 落在留出年度的 chunk,回傳可入庫清單。"""
    kept = [r for r in rows if str(r.get("metadata", {}).get("year", "")) not in HOLDOUT_YEARS]
    dropped = len(rows) - len(kept)
    if dropped:
        print(f"[HOLDOUT] 排除 {dropped} 筆留出年度({'/'.join(sorted(HOLDOUT_YEARS))})chunk,不入庫")
    return kept


def vector_literal(vec: list) -> str:
    return "[" + ",".join(str(x) for x in vec) + "]"


def clean_text(s: str) -> str:
    """PostgreSQL text 欄位不允許 NUL(0x00)位元組,來源 PDF 解析偶爾夾帶,插入前剔除。"""
    return s.replace("\x00", "")


def embed_batch(http_client: httpx.Client, texts: list) -> list:
    resp = http_client.post("/api/embed", json={"model": LOCAL_EMBED_MODEL, "input": texts})
    resp.raise_for_status()
    return resp.json()["embeddings"]


def ingest_chunks(conn, http_client: httpx.Client, table: str, rows: list, label: str) -> int:
    count = 0
    for i in range(0, len(rows), EMBED_BATCH_SIZE):
        batch = rows[i : i + EMBED_BATCH_SIZE]
        texts = [clean_text(r.get("text", "")) for r in batch]  # embedding 與入庫用同一份清理後文字
        embeddings = embed_batch(http_client, texts)
        with conn.cursor() as cur:
            for row, text, vec in zip(batch, texts, embeddings):
                cur.execute(
                    f"""
                    INSERT INTO {table} (id, text, metadata, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    ON CONFLICT (id) DO UPDATE SET
                        text = EXCLUDED.text,
                        metadata = EXCLUDED.metadata,
                        embedding = EXCLUDED.embedding
                    """,
                    (row["id"], text, json.dumps(row.get("metadata", {}), ensure_ascii=False), vector_literal(vec)),
                )
        conn.commit()
        count += len(batch)
        print(f"  [PROGRESS] {label}: {count}/{len(rows)}")
    return count


def ingest_law_articles(conn, rows: list) -> int:
    count = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                INSERT INTO law_articles (law_article, text, metadata)
                VALUES (%s, %s, %s)
                ON CONFLICT (law_article) DO UPDATE SET
                    text = EXCLUDED.text,
                    metadata = EXCLUDED.metadata
                """,
                (row["id"], clean_text(row.get("text", "")), json.dumps(row.get("metadata", {}), ensure_ascii=False)),
            )
            count += 1
    conn.commit()
    print(f"  [PROGRESS] law_articles: {count}/{len(rows)}")
    return count


def table_count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def main():
    if not POSTGRES_URL:
        raise SystemExit("POSTGRES_URL 未設定")
    law_rows = load_jsonl(LAW_CHUNKS_PATH)
    interp_rows = load_jsonl(INTERP_CHUNKS_PATH)
    case_rows = drop_holdout_years(load_jsonl(CASE_CHUNKS_PATH))
    answer_rows = load_jsonl(ANSWER_CHUNKS_PATH)

    conn = psycopg.connect(POSTGRES_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
        print("[DDL] 已確認 extension/tables 存在")

        with httpx.Client(base_url=LOCAL_LLM_BASE_URL, timeout=LOCAL_LLM_TIMEOUT) as http_client:
            print(f"開始匯入 law_chunks(law+interp): {len(law_rows) + len(interp_rows)} 筆")
            ingest_chunks(conn, http_client, "law_chunks", law_rows + interp_rows, "law_chunks")

            print(f"開始匯入 case_chunks: {len(case_rows)} 筆")
            ingest_chunks(conn, http_client, "case_chunks", case_rows, "case_chunks")

            print(f"開始匯入 answer_chunks: {len(answer_rows)} 筆")
            ingest_chunks(conn, http_client, "answer_chunks", answer_rows, "answer_chunks")

        print(f"開始匯入 law_articles(law_chunks.jsonl): {len(law_rows)} 筆")
        ingest_law_articles(conn, law_rows)

        print(
            "[DONE] "
            f"law_chunks={table_count(conn, 'law_chunks')} "
            f"case_chunks={table_count(conn, 'case_chunks')} "
            f"answer_chunks={table_count(conn, 'answer_chunks')} "
            f"law_articles={table_count(conn, 'law_articles')}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as e:
        print(f"[ERROR] ollama 呼叫失敗: {e}", file=sys.stderr)
        sys.exit(1)
    except psycopg.Error as e:
        print(f"[ERROR] Postgres 錯誤: {e}", file=sys.stderr)
        sys.exit(1)
