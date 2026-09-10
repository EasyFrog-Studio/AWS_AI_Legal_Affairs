"""API Key 驗證 dependency。"""
from typing import Optional

from fastapi import Header, HTTPException, status

from app.config import settings


def require_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> str:
    # 未設定伺服器端 API_KEY 時一律拒絕:否則空字串 header 會等於空字串設定值而放行
    if not settings.API_KEY or x_api_key != settings.API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing X-API-Key")
    return x_api_key
