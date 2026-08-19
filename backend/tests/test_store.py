import threading

from app.models import Case
from app.store import MemoryStore


def _case(case_id="c-aaaaaaaa", title="測試案件"):
    return Case(
        case_id=case_id,
        created_at="2026-08-17T00:00:00",
        title=title,
        source="text",
        input_text="訴願書內容",
    )


def test_create_and_get():
    store = MemoryStore()
    case = _case()
    store.create(case)
    fetched = store.get(case.case_id)
    assert fetched is not None
    assert fetched.case_id == case.case_id
    assert fetched.title == "測試案件"


def test_get_missing_returns_none():
    store = MemoryStore()
    assert store.get("c-nonexist") is None


def test_list_cases():
    store = MemoryStore()
    store.create(_case("c-11111111", "案件一"))
    store.create(_case("c-22222222", "案件二"))
    cases = store.list_cases()
    assert {c.case_id for c in cases} == {"c-11111111", "c-22222222"}


def test_update_merges_fields():
    store = MemoryStore()
    case = _case()
    store.create(case)
    updated = store.update(case.case_id, {"status": "done", "current_stage": "done"})
    assert updated.status == "done"
    assert updated.current_stage == "done"
    fetched = store.get(case.case_id)
    assert fetched.status == "done"
    # 其他欄位不受影響
    assert fetched.title == "測試案件"


def test_thread_safety_many_updates():
    store = MemoryStore()
    case = _case()
    store.create(case)

    def worker():
        for _ in range(50):
            store.update(case.case_id, {"error": None})

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert store.get(case.case_id) is not None
