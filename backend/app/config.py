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
    API_KEY: str = ""  # 空字串=未設定,require_api_key 一律拒絕(見 app/auth.py)
    AWS_REGION: str = "us-west-2"
    BEDROCK_MODEL_ID: str = "us.anthropic.claude-sonnet-4-6"
    # 相鄰兩次 Bedrock 請求的最小間隔秒數;黑客松規範要求 Bedrock 請求控制在 1 RPS 以下
    BEDROCK_MIN_INTERVAL_SECONDS: float = 1.0
    # 單次 Bedrock 回應的等待上限;F1 逐欄擷取大份卷證常超過 botocore 預設的 60 秒
    BEDROCK_READ_TIMEOUT_SECONDS: float = 300
    BEDROCK_CONNECT_TIMEOUT_SECONDS: float = 10
    # botocore 內部重試會在同一次速率閘放行內重發請求,設 1 等於關掉它,讓每次 acquire() 只對應一個請求
    BEDROCK_MAX_ATTEMPTS: int = 1
    KB_LAW_ID: str = ""
    KB_CASE_ID: str = ""
    S3_BUCKET: str = ""
    DDB_LAW_TABLE: str = "appeal_law_articles"
    # 法條表以「法規名稱#條號」為主鍵時留空;該鍵只是 GSI 時填索引名,查詢改走逐鍵 query
    DDB_LAW_INDEX: str = ""
    # 法條修正日期的屬性名;不同來源建的表欄位名不同,查錯欄位會讓修正日期整批落空
    DDB_LAW_DATE_FIELD: str = "amend_date"
    DDB_CASE_TABLE: str = "appeal_cases"
    APPEAL_AGENCY_LOCATION: str = "新北市"  # 受理訴願機關所在地,查在途期間對照表用
    # 定稿 PDF 的落地位置(mock/local 模式;aws 模式落 S3 的 finalized/ 前綴)。
    # 以 backend/ 為基準而非 _REPO_ROOT:容器只把 backend/app COPY 進 /app/app,
    # _REPO_ROOT 在容器內會算成 "/",落成 /finalized 而 appuser 無權建立(回 500)。
    FINALIZED_DIR: str = str(_BACKEND_DIR / "finalized")
    # 上傳 PDF 的落地位置(mock/local 模式;aws 模式落 S3 cases/ 前綴)。與 FINALIZED_DIR 不同,
    # 這份要跨重啟保留供承辦人預覽,compose 掛 volume,故預設值走容器慣用的 /data 前綴而非 backend/ 內
    CASE_FILES_DIR: str = "/data/case_files"
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
    # ollama 單次請求的逾時秒數。地端模型在慢機器上生一份 F4 草稿可達數分鐘,
    # 批次執行更久;調小會讓正常的慢生成被誤判成失敗
    LOCAL_LLM_TIMEOUT: float = 300
    POSTGRES_URL: str = ""  # 空字串=未設定,連線前由呼叫端(store.py/providers/local.py)拒絕
    # 決定書 PDF 的內嵌字型;檔案不存在時 pdf_render 退回 fitz 內建 china-t
    DECISION_FONT_FILE: str = "/usr/share/fonts/truetype/uming-tw.ttf"

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
