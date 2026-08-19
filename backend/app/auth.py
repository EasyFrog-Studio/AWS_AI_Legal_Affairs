"""API Key 驗證 dependency(見 DECISIONS.md「Backend API 契約」節)。"""
from typing import Optional

from fastapi import Header, HTTPException, status

from app.config import settings


def require_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> str:
    if x_api_key != settings.API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing X-API-Key")
    return x_api_key
