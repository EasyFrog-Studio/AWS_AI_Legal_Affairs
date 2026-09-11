"""PostgresStore 單元測試:注入 fake DB-API connection,驗證 SQL 組裝與序列化,不做真實連線。"""
import json

import pytest

from app.config import Settings
from app.models import Case
from app.store import PostgresStore, get_store


def _case(case_id="c-abc12345", title="測試案件"):
    return Case(
        case_id=case_id,
        created_at="2026-08-17T00:00:00",
        title=title,
        status="processing",
        current_stage="f1",
        source="text",
        input_text="訴願書內容",
    )


class FakeCursor:
    def __init__(self, store_rows: dict):
        self.executed: list[tuple] = []
        self._store_rows = store_rows
        self._result: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        upper = sql.strip().upper()
        if upper.startswith("CREATE TABLE"):
            return
        if upper.startswith("INSERT"):
            case_id, data = params
            self._store_rows[case_id] = data
            return
        if upper.startswith("SELECT DATA FROM APPEAL_CASES WHERE"):
            case_id = params[0]
            row = self._store_rows.get(case_id)
            self._result = [(row,)] if row is not None else []
            return
        if upper.startswith("SELECT DATA FROM APPEAL_CASES"):
            self._result = [(v,) for v in self._store_rows.values()]
            return
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


class FakeConnection:
    def __init__(self):
        self.rows: dict[str, str] = {}
        self.commit_count = 0
        self.cursors: list[FakeCursor] = []

    def cursor(self):
        cur = FakeCursor(self.rows)
        self.cursors.append(cur)
        return cur

    def commit(self):
        self.commit_count += 1


def _all_executed(conn: FakeConnection):
    executed = []
    for cur in conn.cursors:
        executed.extend(cur.executed)
    return executed


# ---------- config ----------


def test_ai_provider_local_case_store_empty_gives_postgres_kind():
    settings = Settings(AI_PROVIDER="local", CASE_STORE="")
    assert settings.case_store_kind == "postgres"


def test_case_store_explicit_value_wins_over_local_provider():
    settings = Settings(AI_PROVIDER="local", CASE_STORE="memory")
    assert settings.case_store_kind == "memory"


def test_aws_and_mock_case_store_kind_unaffected():
    assert Settings(AI_PROVIDER="aws", CASE_STORE="").case_store_kind == "dynamodb"
    assert Settings(AI_PROVIDER="mock", CASE_STORE="").case_store_kind == "memory"


# ---------- PostgresStore init ----------


def test_init_creates_table_if_not_exists():
    conn = FakeConnection()
    PostgresStore("postgresql://fake", connect=lambda: conn)
    executed_sql = [sql.upper() for sql, _ in _all_executed(conn)]
    assert any(
        "CREATE TABLE IF NOT EXISTS APPEAL_CASES" in sql for sql in executed_sql
    )
    assert conn.commit_count >= 1


def test_init_does_not_import_psycopg_when_connect_injected():
    import sys

    assert "psycopg" not in sys.modules
    conn = FakeConnection()
    PostgresStore("postgresql://fake", connect=lambda: conn)
    assert "psycopg" not in sys.modules


def test_init_raises_runtime_error_when_url_is_empty():
    with pytest.raises(RuntimeError, match="POSTGRES_URL"):
        PostgresStore("")


# ---------- CRUD ----------


def test_create_and_get_roundtrip():
    conn = FakeConnection()
    store = PostgresStore("postgresql://fake", connect=lambda: conn)
    case = _case()
    store.create(case)

    fetched = store.get(case.case_id)
    assert fetched is not None
    assert fetched.case_id == case.case_id
    assert fetched.title == "測試案件"


def test_create_stores_whole_case_as_jsonb_via_json_dumps():
    from app.models import CaseInfo

    conn = FakeConnection()
    store = PostgresStore("postgresql://fake", connect=lambda: conn)
    case = _case().model_copy(
        update={
            "f1": CaseInfo(
                appellant="王小明",
                agency="環保署",
                disposition_date="110年1月1日",
                disposition_no="環署字第1號",
                disposition_summary="罰鍰處分",
                case_type="廢棄物清理",
            )
        }
    )
    store.create(case)

    raw = conn.rows[case.case_id]
    assert isinstance(raw, str)
    parsed = json.loads(raw)
    assert parsed["f1"]["appellant"] == "王小明"

    fetched = store.get(case.case_id)
    assert fetched.f1.appellant == "王小明"
    assert fetched.f1.agency == "環保署"


def test_get_missing_returns_none():
    conn = FakeConnection()
    store = PostgresStore("postgresql://fake", connect=lambda: conn)
    assert store.get("c-nonexist") is None


def test_list_cases_returns_all():
    conn = FakeConnection()
    store = PostgresStore("postgresql://fake", connect=lambda: conn)
    store.create(_case("c-11111111", "案件一"))
    store.create(_case("c-22222222", "案件二"))
    cases = store.list_cases()
    assert {c.case_id for c in cases} == {"c-11111111", "c-22222222"}


def test_update_merges_fields_and_persists():
    conn = FakeConnection()
    store = PostgresStore("postgresql://fake", connect=lambda: conn)
    case = _case()
    store.create(case)

    updated = store.update(case.case_id, {"status": "done", "current_stage": "done"})
    assert updated.status == "done"
    assert updated.current_stage == "done"

    fetched = store.get(case.case_id)
    assert fetched.status == "done"
    assert fetched.title == "測試案件"


def test_update_missing_raises_keyerror():
    conn = FakeConnection()
    store = PostgresStore("postgresql://fake", connect=lambda: conn)
    try:
        store.update("c-nonexist", {"status": "done"})
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_create_uses_upsert_on_conflict():
    conn = FakeConnection()
    store = PostgresStore("postgresql://fake", connect=lambda: conn)
    case = _case()
    store.create(case)
    store.create(case)  # 第二次 create 同一 case_id,模擬 update 內部 upsert

    insert_calls = [sql for sql, _ in _all_executed(conn) if sql.strip().upper().startswith("INSERT")]
    assert len(insert_calls) == 2
    assert "ON CONFLICT" in insert_calls[0].upper()


def test_commit_called_on_each_write():
    conn = FakeConnection()
    store = PostgresStore("postgresql://fake", connect=lambda: conn)
    before = conn.commit_count
    store.create(_case())
    assert conn.commit_count > before


# ---------- factory ----------


def test_get_store_returns_postgres_store_for_postgres_kind(monkeypatch):
    import app.store as store_module

    monkeypatch.setattr(store_module.settings, "CASE_STORE", "postgres")
    monkeypatch.setattr(store_module.settings, "POSTGRES_URL", "postgresql://u:p@h/db")
    fake_conn = FakeConnection()
    monkeypatch.setattr(
        store_module, "PostgresStore", lambda url, connect=None: PostgresStore(url, connect=lambda: fake_conn)
    )
    result = get_store()
    assert isinstance(result, PostgresStore)
