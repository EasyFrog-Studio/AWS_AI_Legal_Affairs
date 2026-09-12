"""aws/local 共用的 F1 JSON schema 產生器,欄位清單推導自 CaseInfo 本身,不手寫第二份。"""
from typing import get_origin

from app.models import AGENT_ROLES, SERVICE_METHODS, CaseInfo

_ENUM_BY_FIELD = {
    "service_method": list(SERVICE_METHODS),
    "agent_role": list(AGENT_ROLES),
}


def case_info_json_schema() -> dict:
    """CaseInfo 的全部欄位一律必填(理由見 aws.py extract_case_info 的既有註解:
    選填時模型會整個省略該鍵,程式化檢核就看不出「沒抽到」與「卷內沒有」的差別)。"""
    properties: dict = {}
    for name, field in CaseInfo.model_fields.items():
        if get_origin(field.annotation) is list:  # CaseInfo 目前所有 list 欄位皆為 list[str]
            properties[name] = {"type": "array", "items": {"type": "string"}}
        else:
            prop: dict = {"type": "string"}
            if name in _ENUM_BY_FIELD:
                prop["enum"] = _ENUM_BY_FIELD[name]
            properties[name] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": list(CaseInfo.model_fields.keys()),
    }
