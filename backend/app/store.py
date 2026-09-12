"""CaseStore 抽象 + MemoryStore / DynamoDBStore / PostgresStore。"""
from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from typing import Optional

from app.config import settings
from app.models import Case, CaseDocument, build_input_text

# 巢狀欄位在 DynamoDB 存放時序列化為 JSON 字串,
# 避免巢狀 float 落地成 DynamoDB Decimal 造成的型別問題。
# documents 預設值是 {} 不是 None,故走與 f1/screening 等欄位相同的「非 None 就序列化」路徑仍正確——
# 序列化後是 "{}" 而非空字串,還原時 `if raw else None` 對非空字串一律走 json.loads,{} 也不例外。
_JSON_FIELDS = (
    "documents",
    "f1",
    "f1_system",
    "screening",
    "screening_system",
    "screening_input_f1",
    "retrieval_input_screening",
    "deadline",
    "f2",
    "f2_refs",
    "f3",
    "f4",
    "f4_system",
    "decision_header",
    "draft_versions",
)
# 空清單/空 dict 的欄位:還原時的空值是 [] 或 {},不是 None(型別非 Optional,None 會驗證失敗)
_EMPTY_ON_READ = {"documents": dict, "draft_versions": list, "decision_header": dict}

# DynamoDB 單筆項目上限 400KB;留 buffer 擋在 350KB,超過就在寫入前 raise 帶中文訊息的例外,
# 不讓 boto3 的 ValidationException 在背景任務裡把案件打成 status=error
MAX_ITEM_BYTES = 350 * 1024


class CaseTooLargeError(Exception):
    """卷證文字量超過單筆儲存上限。API 層轉 400,訊息要講得出是什麼超了。"""


def item_size_bytes(item: dict) -> int:
    """DynamoDB 計算項目大小的方式:屬性名 + 屬性值的 UTF-8 位元組數總和。"""
    return sum(len(str(key).encode("utf-8")) + len(str(value).encode("utf-8")) for key, value in item.items())


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
    """appeal_cases 表,PK 為 case_id。"""

    def __init__(self, table=None) -> None:
        if table is None:
            import boto3

            table = boto3.resource("dynamodb", region_name=settings.AWS_REGION).Table(
                settings.DDB_CASE_TABLE
            )
        self._table = table

    def _to_item(self, case: Case) -> dict:
        d = case.model_dump(mode="json")
        item = {
            "case_id": d["case_id"],
            "created_at": d["created_at"],
            "title": d["title"],
            "status": d["status"],
            "current_stage": d["current_stage"],
            "track": d["track"] or "",
            "source": d["source"],
            # documents 有值時不另存 input_text:它是衍生值,兩份都存等於把卷證文字量加倍,
            # 而 400KB 的單筆上限最可能被掃描件 OCR 全文撐爆。舊資料(無 documents)才留原字串。
            "input_text": "" if d["documents"] else d["input_text"],
            "finalized_at": d["finalized_at"] or "",
            "draft_versions_truncated": "1" if d["draft_versions_truncated"] else "",
            "f1_edited": "1" if d["f1_edited"] else "",
            "draft_plain_text": d["draft_plain_text"],
            "error": d["error"] or "",
        }
        for k in _JSON_FIELDS:
            v = d.get(k)
            item[k] = json.dumps(v, ensure_ascii=False) if v is not None else ""

        size = item_size_bytes(item)
        if size > MAX_ITEM_BYTES:
            raise CaseTooLargeError(
                f"卷證文字量超過單筆儲存上限（{size} bytes，上限 {MAX_ITEM_BYTES} bytes）"
            )
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
            "finalized_at": item.get("finalized_at") or None,
            "draft_versions_truncated": bool(item.get("draft_versions_truncated")),
            "f1_edited": bool(item.get("f1_edited")),
            "draft_plain_text": item.get("draft_plain_text") or "",
            "error": item.get("error") or None,
        }
        for k in _JSON_FIELDS:
            raw = item.get(k, "")
            # documents/draft_versions 的型別非 Optional,舊資料沒有這個 key 時不能落成 None
            # ——Case 建構會直接驗證失敗;其餘欄位是 Optional,None 才是正確空值
            empty = _EMPTY_ON_READ.get(k)
            data[k] = json.loads(raw) if raw else (empty() if empty else None)

        documents = {slot: CaseDocument(**doc) for slot, doc in data["documents"].items()}
        # documents 有值就重建;沒有(加這欄之前寫入的舊資料)才沿用 item 裡的 input_text
        data["input_text"] = build_input_text(documents) if documents else item.get("input_text", "")
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


class PostgresStore(CaseStore):
    """appeal_cases 表,案件整包以 JSONB 存。"""

    def __init__(self, url, connect=None) -> None:
        if not url:
            raise RuntimeError("POSTGRES_URL 未設定，local 模式需要 Postgres 連線字串")
        if connect is None:

            def connect():
                import psycopg

                # autocommit:讀路徑不留 idle-in-transaction、失敗不毒化連線;
                # 寫入路徑的顯式 commit() 在 autocommit 下為無害的 no-op
                return psycopg.connect(url, autocommit=True)

        self._conn = connect()
        self._init_table()

    def _init_table(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS appeal_cases ("
            "case_id TEXT PRIMARY KEY, data JSONB NOT NULL)"
        )
        self._conn.commit()

    def create(self, case: Case) -> None:
        data = json.dumps(case.model_dump(mode="json"), ensure_ascii=False)
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO appeal_cases (case_id, data) VALUES (%s, %s) "
            "ON CONFLICT (case_id) DO UPDATE SET data = EXCLUDED.data",
            (case.case_id, data),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_case(row) -> Case:
        data = row[0]
        if isinstance(data, str):  # psycopg 回 dict,注入的 fake 可能回 JSON 字串
            data = json.loads(data)
        return Case(**data)

    def get(self, case_id: str) -> Optional[Case]:
        cur = self._conn.cursor()
        cur.execute("SELECT data FROM appeal_cases WHERE case_id = %s", (case_id,))
        row = cur.fetchone()
        return self._row_to_case(row) if row is not None else None

    def list_cases(self) -> list[Case]:
        cur = self._conn.cursor()
        cur.execute("SELECT data FROM appeal_cases")
        return [self._row_to_case(row) for row in cur.fetchall()]

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
    if settings.case_store_kind == "postgres":
        return PostgresStore(settings.POSTGRES_URL)
    return MemoryStore()
