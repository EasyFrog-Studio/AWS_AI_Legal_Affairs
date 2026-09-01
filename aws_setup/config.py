"""aws_setup 共用常數與 resources.json 讀寫。"""
import json
from pathlib import Path

# --- Account / region ---
ACCOUNT_ID = "047877300727"
REGION = "us-west-2"

# --- S3 ---
S3_BUCKET = f"appeal-ai-{ACCOUNT_ID}"

# --- S3 Vectors ---
VECTOR_BUCKET = "appeal-ai-vectors"
INDEX_LAW = "kb-law-index"
INDEX_CASE = "kb-case-index"
VECTOR_DIMENSION = 1024
VECTOR_DISTANCE_METRIC = "cosine"

# --- Bedrock Knowledge Base ---
KB_LAW_NAME = "appeal-kb-law"
KB_CASE_NAME = "appeal-kb-case"
IAM_ROLE_NAME = "appeal-kb-role"
EMBEDDING_MODEL_ID = "cohere.embed-multilingual-v3"
EMBEDDING_MODEL_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/{EMBEDDING_MODEL_ID}"
EMBEDDING_DIMENSION = 1024

# --- DynamoDB ---
DDB_LAW_TABLE = "appeal_law_articles"
DDB_CASE_TABLE = "appeal_cases"

# --- Paths ---
AWS_SETUP_DIR = Path(__file__).resolve().parent
PROJECT_AREA_DIR = AWS_SETUP_DIR.parent
DATASET_DIR = PROJECT_AREA_DIR.parent / "data"
PREPROCESSING_OUTPUT_DIR = PROJECT_AREA_DIR.parent / "data" / "output"
LAW_CHUNKS_PATH = PREPROCESSING_OUTPUT_DIR / "law_chunks.jsonl"
CASE_CHUNKS_PATH = PREPROCESSING_OUTPUT_DIR / "case_chunks.jsonl"
INTERP_CHUNKS_PATH = PREPROCESSING_OUTPUT_DIR / "interp_chunks.jsonl"
MARKDOWN_DIR = PREPROCESSING_OUTPUT_DIR / "markdown"

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
