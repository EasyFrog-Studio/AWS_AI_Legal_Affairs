"""aws_setup 共用常數與 resources.json 讀寫。"""
import json
import os
from pathlib import Path

# --- Account / region ---
ACCOUNT_ID = "000000000000"
REGION = "us-west-2"

# --- S3 ---
S3_BUCKET = f"appeal-ai-{ACCOUNT_ID}"

# --- S3 Vectors ---
VECTOR_BUCKET = "appeal-ai-vectors"
INDEX_LAW = "kb-law-index"
INDEX_CASE = "kb-case-index"
INDEX_INTERPRETATION = "kb-interpretation-index"
INDEX_RULING = "kb-ruling-index"
INDEX_JUDGMENT = "kb-judgment-index"
VECTOR_DIMENSION = 1024
VECTOR_DISTANCE_METRIC = "cosine"

# --- Bedrock Knowledge Base ---
KB_LAW_NAME = "appeal-kb-law"
KB_CASE_NAME = "appeal-kb-case"
KB_INTERPRETATION_NAME = "appeal-kb-interpretation"
KB_RULING_NAME = "appeal-kb-ruling"
KB_JUDGMENT_NAME = "appeal-kb-judgment"
IAM_ROLE_NAME = "appeal-kb-role"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_MODEL_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/{EMBEDDING_MODEL_ID}"
EMBEDDING_DIMENSION = 1024

# --- DynamoDB ---
DDB_LAW_TABLE = "appeal_law_articles"
DDB_CASE_TABLE = "appeal_cases"
DDB_INTERPRETATION_TABLE = "appeal_interpretations"
DDB_RULING_TABLE = "appeal_rulings"
DDB_JUDGMENT_TABLE = "appeal_judgments"
DDB_PAST_DECISIONS_TABLE = "appeal_past_decisions"

# 參考見解三類的 (doc_kind, KB 名, index 名, DDB 表名, chunk 檔名) 對應;新增一類只改這裡
REFERENCE_KINDS = (
    ("司法院釋字", KB_INTERPRETATION_NAME, INDEX_INTERPRETATION, DDB_INTERPRETATION_TABLE),
    ("行政函釋", KB_RULING_NAME, INDEX_RULING, DDB_RULING_TABLE),
    ("行政法院裁判", KB_JUDGMENT_NAME, INDEX_JUDGMENT, DDB_JUDGMENT_TABLE),
)
REFERENCE_DDB_PK = "ref_id"

# --- Paths ---
AWS_SETUP_DIR = Path(__file__).resolve().parent
PROJECT_AREA_DIR = AWS_SETUP_DIR.parent
DATASET_DIR = PROJECT_AREA_DIR.parent / "data"
PREPROCESSING_OUTPUT_DIR = PROJECT_AREA_DIR.parent / "data" / "output"
LAW_CHUNKS_PATH = PREPROCESSING_OUTPUT_DIR / "law_chunks.jsonl"
CASE_CHUNKS_PATH = PREPROCESSING_OUTPUT_DIR / "case_chunks.jsonl"
INTERP_CHUNKS_PATH = PREPROCESSING_OUTPUT_DIR / "interp_chunks.jsonl"
MARKDOWN_DIR = PREPROCESSING_OUTPUT_DIR / "markdown"

# 爬蟲語料位置:預設在專案 data/ 下,語料未就位時以環境變數指向實際存放處
REFERENCE_CHUNK_DIR = Path(
    os.environ.get("CRAWL_REF_CHUNK_DIR", DATASET_DIR / "爬蟲集" / "chunk資料" / "參考資料")
)
# 爬蟲決定書 chunk 位置;與參考見解同一套環境變數覆蓋規則
CRAWL_CASE_CHUNK_DIR = Path(
    os.environ.get("CRAWL_CASE_CHUNK_DIR", DATASET_DIR / "爬蟲集" / "chunk資料" / "訴願決定書")
)
REFERENCE_PDF_DIR = Path(
    os.environ.get("CRAWL_REF_PDF_DIR", DATASET_DIR / "爬蟲集" / "爬蟲原資料+官方資料" / "參考資料")
)
REFERENCE_S3_PREFIX = "reference"

RESOURCES_PATH = AWS_SETUP_DIR / "resources.json"
INGEST_FAILURES_PATH = AWS_SETUP_DIR / "ingest_failures.json"


def load_resources() -> dict:
    """讀 aws_setup/resources.json,不存在回傳空 dict。"""
    if not RESOURCES_PATH.exists():
        return {}
    with RESOURCES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_resources(updates: dict) -> None:
    """把 updates 併入既有 resources.json 後寫回(不覆蓋其他腳本已寫入的 key)。"""
    resources = load_resources()
    resources.update(updates)
    with RESOURCES_PATH.open("w", encoding="utf-8") as f:
        json.dump(resources, f, ensure_ascii=False, indent=2)
