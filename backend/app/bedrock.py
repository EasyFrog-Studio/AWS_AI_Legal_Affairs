"""Bedrock 用戶端的共用連線設定:F1 從四份卷證逐欄擷取六十幾個欄位會超過 botocore 預設的 60 秒讀取逾時。"""
from botocore.config import Config

from app.config import settings


def bedrock_config() -> Config:
    return Config(
        connect_timeout=settings.BEDROCK_CONNECT_TIMEOUT_SECONDS,
        read_timeout=settings.BEDROCK_READ_TIMEOUT_SECONDS,
        retries={"max_attempts": settings.BEDROCK_MAX_ATTEMPTS, "mode": "standard"},
    )
