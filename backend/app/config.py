"""集中讀取環境變數設定(見 DECISIONS.md「環境變數」節)。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parent=app, parent.parent=backend, parent.parent.parent=LRB_automation(repo root)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
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
    CASE_STORE: str = ""  # memory|dynamodb|postgres; 空字串代表依 AI_PROVIDER 決定預設值

    # 非契約環境變數(實作細節):mock 樣本資料目錄
    MOCK_DATA_DIR: str = _DEFAULT_MOCK_DATA_DIR

    # local 模式(見 DECISIONS.md「AI_PROVIDER=local 地端模式」)
    LOCAL_LLM_BASE_URL: str = "http://host.docker.internal:11434"
    LOCAL_LLM_MODEL: str = "kamekichi128/qwen3-4b-instruct-2507"
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
