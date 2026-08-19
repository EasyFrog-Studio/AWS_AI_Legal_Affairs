"""CaseStore 抽象 + MemoryStore + DynamoDBStore(見 DECISIONS.md「CaseStore 介面」節)。"""
from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from typing import Optional

from app.config import settings
from app.models import Case

# f1/screening/f2/f3/f4 為巢狀結構,DynamoDB 存放時序列化為 JSON 字串,
# 避免巢狀 float 落地成 DynamoDB Decimal 造成的型別問題。
_JSON_FIELDS = ("f1", "screening", "f2", "f3", "f4")


class CaseStore(ABC):
    @abstractmethod
    def create(self, case: Case) -> None: ...

    @abstractmethod
    def get(self, case_id: str) -> Optional[Case]: ...

    @abstractmethod
    def list_cases(self) -> list[Case]: ...

    @abstractmethod
    def update(self, case_id: str, fields: dict) -> Case: ...


class MemoryStore(CaseStore):
    """單一 process 內以 dict 存放,用 lock 確保 thread-safe。"""

    def __init__(self) -> None:
        self._data: dict[str, Case] = {}
        self._lock = threading.Lock()

    def create(self, case: Case) -> None:
        with self._lock:
            self._data[case.case_id] = case

    def get(self, case_id: str) -> Optional[Case]:
        with self._lock:
            return self._data.get(case_id)

    def list_cases(self) -> list[Case]:
        with self._lock:
            return list(self._data.values())

    def update(self, case_id: str, fields: dict) -> Case:
        with self._lock:
            case = self._data[case_id]
            updated = case.model_copy(update=fields)
            self._data[case_id] = updated
            return updated


class DynamoDBStore(CaseStore):
    """PK 為 case_id(見 DECISIONS.md 「appeal_cases」表)。"""

    def __init__(self, table=None) -> None:
        if table is None:
            import boto3

            table = boto3.resource("dynamodb", region_name=settings.AWS_REGION).Table(
                settings.DDB_CASE_TABLE
            )
        self._table = table

    def _to_item(self, case: Case) -> dict:
        d = case.model_dump()
        item = {
            "case_id": d["case_id"],
            "created_at": d["created_at"],
            "title": d["title"],
            "status": d["status"],
            "current_stage": d["current_stage"],
            "track": d["track"] or "",
            "source": d["source"],
            "input_text": d["input_text"],
            "error": d["error"] or "",
        }
        for k in _JSON_FIELDS:
            v = d.get(k)
            item[k] = json.dumps(v, ensure_ascii=False) if v is not None else ""
        return item

    def _from_item(self, item: dict) -> Case:
        data = {
            "case_id": item["case_id"],
            "created_at": item.get("created_at", ""),
            "title": item.get("title", ""),
            "status": item.get("status") or "processing",
            "current_stage": item.get("current_stage") or "f1",
            "track": item.get("track") or None,
            "source": item.get("source") or "text",
            "input_text": item.get("input_text", ""),
            "error": item.get("error") or None,
        }
        for k in _JSON_FIELDS:
            raw = item.get(k, "")
            data[k] = json.loads(raw) if raw else None
        return Case(**data)

    def create(self, case: Case) -> None:
        self._table.put_item(Item=self._to_item(case))

    def get(self, case_id: str) -> Optional[Case]:
        resp = self._table.get_item(Key={"case_id": case_id})
        item = resp.get("Item")
        return self._from_item(item) if item else None

    def list_cases(self) -> list[Case]:
        items: list[dict] = []
        resp = self._table.scan()
        items.extend(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = self._table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
            items.extend(resp.get("Items", []))
        return [self._from_item(i) for i in items]

    def update(self, case_id: str, fields: dict) -> Case:
        case = self.get(case_id)
        if case is None:
            raise KeyError(case_id)
        updated = case.model_copy(update=fields)
        self.create(updated)
        return updated


def get_store() -> CaseStore:
    """依 settings.case_store_kind(env CASE_STORE,預設依 AI_PROVIDER)選實作。"""
    if settings.case_store_kind == "dynamodb":
        return DynamoDBStore()
    return MemoryStore()
