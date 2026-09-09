"""集中讀取環境變數設定。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parent=app, parent.parent=backend(容器內即 /app),parent.parent.parent=repo root
# 要寫檔的預設值一律以 _BACKEND_DIR 為基準:容器只 COPY backend/app 進 /app/app,
# _REPO_ROOT 在容器內會算成 "/",拿它當輸出目錄會落在檔案系統根而 appuser 無權建立
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
_DEFAULT_MOCK_DATA_DIR = str(_REPO_ROOT / "data_show" / "sample_appeals")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    AI_PROVIDER: str = "mock"  # mock|aws|local
    API_KEY: str = "demo-key-2026"
    AWS_REGION: str = "us-west-2"
    BEDROCK_MODEL_ID: str = "us.anthropic.claude-sonnet-4-6"
    KB_LAW_ID: str = ""
    KB_CASE_ID: str = ""
    S3_BUCKET: str = ""
    DDB_LAW_TABLE: str = "appeal_law_articles"
    DDB_CASE_TABLE: str = "appeal_cases"
    APPEAL_AGENCY_LOCATION: str = "新北市"  # 受理訴願機關所在地,查在途期間對照表用
    # 定稿 PDF 的落地位置(mock/local 模式;aws 模式落 S3 的 finalized/ 前綴)。
    # 以 backend/ 為基準而非 _REPO_ROOT:容器只把 backend/app COPY 進 /app/app,
    # _REPO_ROOT 在容器內會算成 "/",落成 /finalized 而 appuser 無權建立(實測回 500)。
    FINALIZED_DIR: str = str(_BACKEND_DIR / "finalized")
    CASE_STORE: str = ""  # memory|dynamodb|postgres; 空字串代表依 AI_PROVIDER 決定預設值

    # 文件型態確認:規則判斷不出來時的備援(document_check.py)。空字串代表不啟用,
    # 一律回「無法確認」交人工核對,不因缺金鑰而中斷建案流程。
    GEMINI_API_KEY: str = ""
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # 非契約環境變數(實作細節):mock 樣本資料目錄
    MOCK_DATA_DIR: str = _DEFAULT_MOCK_DATA_DIR

    # local 模式
    LOCAL_LLM_BASE_URL: str = "http://host.docker.internal:11434"
    LOCAL_LLM_MODEL: str = "kamekichi128/qwen3-4b-instruct-2507"
    LOCAL_VISION_MODEL: str = "qwen2.5vl:7b"  # 掃描件逐頁抽字用(app/ocr.py),與文字模型分開設
    LOCAL_EMBED_MODEL: str = "bge-m3"
    POSTGRES_URL: str = "postgresql://appeal:appeal@postgres:5432/appeal"

    @property
    def case_store_kind(self) -> str:
        if self.CASE_STORE:
            return self.CASE_STORE
        if self.AI_PROVIDER == "aws":
            return "dynamodb"
        if self.AI_PROVIDER == "local":
            return "postgres"
        return "memory"


def get_settings() -> Settings:
    return Settings()


settings = get_settings()
