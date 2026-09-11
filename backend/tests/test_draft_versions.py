"""草稿版本留存與定稿標記(不鎖):改得動、追得回,但不擋人。"""
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.models import Case, DraftResult, MAX_DRAFT_VERSIONS


def _headers():
    return {"X-API-Key": settings.API_KEY}


def _client():
    return TestClient(main_module.app)


def _seed(case_id: str):
    main_module.store.create(
        Case(
            case_id=case_id,
            created_at="2026-08-17T00:00:00+00:00",
            title="草稿版本測試案",
            status="done",
            current_stage="done",
            track="inadmissible",
            source="text",
            input_text="測試訴願書內容",
        )
    )
    main_module.store.update(
        case_id,
        {
            "f4": DraftResult(
                draft_type="不受理", fact="", reason="原始理由。", main_text="訴願不受理。", cited_laws=["訴願法#77"]
            )
        },
    )


def _patch(client, case_id: str, reason: str, base_version=None):
    """一次修改 = 一份新的決定書全文;編輯單位是整份,不是三欄。"""
    body = {"text": f"新北市政府訴願決定書 理由 {reason}"}
    if base_version is not None:
        body["base_version"] = base_version
    return client.patch(f"/api/cases/{case_id}/draft-text", json=body, headers=_headers())


def test_each_patch_saves_a_version():
    _seed("c-ver0001")
    client = _client()

    _patch(client, "c-ver0001", "第一次修改。")
    _patch(client, "c-ver0001", "第二次修改。")

    case = client.get("/api/cases/c-ver0001", headers=_headers()).json()
    assert [v["text"].split("理由 ")[-1] for v in case["draft_versions"]] == [
        "第一次修改。",
        "第二次修改。",
    ]
    assert case["draft_plain_text"].endswith("第二次修改。")


def test_stale_base_version_returns_409_and_does_not_write():
    """兩個視窗同時改,後送出的那份不能無聲蓋掉前一份——兩邊都以為自己存成功了是最糟的。"""
    _seed("c-ver0002")
    client = _client()
    _patch(client, "c-ver0002", "甲的修改。", base_version=0)

    resp = _patch(client, "c-ver0002", "乙的修改。", base_version=0)

    assert resp.status_code == 409
    body = resp.json()
    assert "已被他人更新" in body["detail"]["message"]
    assert "甲的修改。" in body["detail"]["text"]  # 回最新內容,前端才提示得出差異
    case = client.get("/api/cases/c-ver0002", headers=_headers()).json()
    assert case["draft_plain_text"].endswith("甲的修改。")  # 未被寫入


def test_matching_base_version_is_accepted():
    _seed("c-ver0003")
    client = _client()
    _patch(client, "c-ver0003", "第一次修改。", base_version=0)

    resp = _patch(client, "c-ver0003", "第二次修改。", base_version=1)

    assert resp.status_code == 200


def test_finalize_only_marks_and_lands_the_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FINALIZED_DIR", str(tmp_path))
    _seed("c-ver0004")
    client = _client()

    resp = client.post("/api/cases/c-ver0004/finalize", headers=_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["finalized_at"]
    assert body["pdf_location"]
    assert (tmp_path / "c-ver0004.pdf").is_file()
    case = client.get("/api/cases/c-ver0004", headers=_headers()).json()
    assert case["finalized_at"] == body["finalized_at"]
    assert case["status"] == "done"  # Status 不新增 finalized,流程不鎖死


def test_patch_after_finalize_still_works_and_saves_a_version(tmp_path, monkeypatch):
    """定稿只是標記,承辦人隨時可再改,每次改存一版。"""
    monkeypatch.setattr(settings, "FINALIZED_DIR", str(tmp_path))
    _seed("c-ver0005")
    client = _client()
    client.post("/api/cases/c-ver0005/finalize", headers=_headers())

    resp = _patch(client, "c-ver0005", "定稿後又改。")

    assert resp.status_code == 200
    case = client.get("/api/cases/c-ver0005", headers=_headers()).json()
    assert len(case["draft_versions"]) == 1
    assert case["finalized_at"]  # 定稿標記不因再修改而消失


def test_finalize_without_a_draft_returns_409():
    main_module.store.create(
        Case(
            case_id="c-ver0006",
            created_at="2026-08-17T00:00:00+00:00",
            title="無草稿",
            status="done",
            current_stage="done",
            source="text",
            input_text="x",
        )
    )

    resp = _client().post("/api/cases/c-ver0006/finalize", headers=_headers())

    assert resp.status_code == 409


def test_finalize_nonexistent_case_returns_404():
    assert _client().post("/api/cases/c-notexist/finalize", headers=_headers()).status_code == 404


def test_default_finalized_dir_is_writable_in_the_container():
    """容器只把 backend/app COPY 進 /app/app,所以 config 的 repo root 在容器內會算成 "/";
    定稿目錄若以它為基準會落成 /finalized,而 image 以 appuser 執行、無權在根目錄建資料夾
    ——docker compose 下會回 500。預設值因此改以 backend/ 為基準(容器內即 /app,已 chown)。"""
    from pathlib import Path

    from app.config import Settings

    default = Path(Settings().FINALIZED_DIR)

    assert default.parent != Path(default.anchor), f"定稿目錄落在檔案系統根:{default}"
    assert default.parent.name == "backend" or (default.parent / "app").is_dir()


def test_version_history_is_capped_and_says_so():
    """版本歷史會單向成長,不設上限就是等它某天在背景任務裡撞上 DynamoDB 400KB 崩掉。
    丟掉最舊的那一版是必要的,但「有版本被丟掉」這件事要看得見。"""
    _seed("c-ver0007")
    client = _client()

    for i in range(MAX_DRAFT_VERSIONS + 2):
        _patch(client, "c-ver0007", f"第{i}次修改。")

    case = client.get("/api/cases/c-ver0007", headers=_headers()).json()
    assert len(case["draft_versions"]) == MAX_DRAFT_VERSIONS
    assert case["draft_versions"][0]["text"].endswith("第2次修改。")  # 最舊兩版已丟
    assert case["draft_versions_truncated"] is True


def test_aws_mode_finalize_uploads_to_s3(monkeypatch):
    """aws 模式的定稿 PDF 落 S3;位址仍由 finalize 回傳,前端不必知道模式差異。"""
    monkeypatch.setattr(settings, "AI_PROVIDER", "aws")
    monkeypatch.setattr(settings, "S3_BUCKET", "appeal-bucket")
    uploaded = {}

    class _FakeS3:
        def put_object(self, **kwargs):
            uploaded.update(kwargs)

    monkeypatch.setattr(main_module, "_s3_client", lambda: _FakeS3())
    _seed("c-ver0008")

    resp = _client().post("/api/cases/c-ver0008/finalize", headers=_headers())

    assert resp.status_code == 200
    assert uploaded["Bucket"] == "appeal-bucket"
    assert uploaded["Key"].endswith("c-ver0008.pdf")
    assert uploaded["Body"].startswith(b"%PDF")
    assert resp.json()["pdf_location"] == f"s3://appeal-bucket/{uploaded['Key']}"
