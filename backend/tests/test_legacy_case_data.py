"""store 內同時存在三欄格式(fact/reason/main_text)的版本快照與沒有全文欄的案件,
任一件讀不出來,案件清單整頁就是 500。"""
import json

from pydantic import ValidationError
import pytest

from app.models import Case, DraftResult, DraftVersion
from app.pdf_render import decision_plain_text
from app.store import PostgresStore


_SAVED_AT = "2026-08-17T00:00:00+00:00"


def _legacy_version(fact: str, reason: str, main_text: str) -> dict:
    return {"saved_at": _SAVED_AT, "fact": fact, "reason": reason, "main_text": main_text}


# ---------- DraftVersion:三欄舊快照轉成單欄全文 ----------


@pytest.mark.parametrize(
    "fact, reason, main_text",
    [
        ("訴願人於110年間遭裁處罰鍰。", "原處分並無違誤。", "訴願駁回。"),
        ("土地經查為訴願人所有。", "建照核發程序合法。", "原處分撤銷。"),
    ],
)
def test_a_legacy_three_field_snapshot_is_flattened_into_text(fact, reason, main_text):
    version = DraftVersion(**_legacy_version(fact, reason, main_text))

    assert version.saved_at == _SAVED_AT
    assert version.text.index(main_text) < version.text.index(fact) < version.text.index(reason)
    for heading in ("主　文", "事　實", "理　由"):
        assert heading in version.text


def test_a_legacy_snapshot_without_fact_omits_the_fact_section():
    """不受理案的事實欄本來就是空的(訴願法§89 I③),不印空標題。"""
    version = DraftVersion(**_legacy_version("", "逾期提起,依訴願法第77條第2款不受理。", "訴願不受理。"))

    assert "事　實" not in version.text
    assert "主　文" in version.text and "理　由" in version.text


def test_a_current_snapshot_keeps_its_text_verbatim():
    version = DraftVersion(saved_at=_SAVED_AT, text="整份全文\n第二段")

    assert version.text == "整份全文\n第二段"


def test_a_snapshot_with_neither_shape_is_still_rejected():
    with pytest.raises(ValidationError):
        DraftVersion(saved_at=_SAVED_AT)


def test_a_legacy_snapshot_without_saved_at_is_rejected_not_silently_dated():
    """攤平只補 text,不替快照編造時間;缺 saved_at 的舊列要看得到錯,不是讀成 None。"""
    with pytest.raises(ValidationError):
        DraftVersion(fact="事實", reason="理由", main_text="訴願駁回。")


# ---------- Case:有 f4 但沒有全文的舊案件,讀出時補全文 ----------


def _f4(main_text: str) -> DraftResult:
    return DraftResult(draft_type="駁回", fact="事實段", reason="理由段", main_text=main_text, cited_laws=[])


@pytest.mark.parametrize("main_text", ["訴願駁回。", "原處分撤銷,由原處分機關另為適法之處分。"])
def test_a_case_with_f4_but_no_plain_text_gets_one_on_read(main_text):
    case = Case(case_id="c-legacy", created_at=_SAVED_AT, title="舊案", source="text", input_text="卷證", f4=_f4(main_text))

    assert case.draft_plain_text == decision_plain_text(case)
    assert main_text in case.draft_plain_text


def test_an_existing_plain_text_is_never_overwritten():
    """承辦人改過的全文才是決定書本身,f4 只是素材,不得被重新攤平蓋掉。"""
    case = Case(
        case_id="c-edited", created_at=_SAVED_AT, title="改過的案", source="text", input_text="卷證",
        f4=_f4("訴願駁回。"), draft_plain_text="承辦人改過的全文",
    )

    assert case.draft_plain_text == "承辦人改過的全文"


def test_a_case_without_f4_keeps_plain_text_empty():
    case = Case(case_id="c-new", created_at=_SAVED_AT, title="新案", source="text", input_text="卷證")

    assert case.draft_plain_text == ""


# ---------- store:整列舊資料讀進來 ----------


def _legacy_row(case_id: str, versions: list[dict]) -> tuple:
    data = {
        "case_id": case_id, "created_at": _SAVED_AT, "title": "舊案", "source": "text", "input_text": "卷證",
        "status": "done", "current_stage": "done", "track": "admissible",
        "f4": {"draft_type": "駁回", "fact": "事實段", "reason": "理由段", "main_text": "訴願駁回。", "cited_laws": []},
        "draft_versions": versions,
    }
    return (json.dumps(data, ensure_ascii=False),)


def test_a_postgres_row_with_legacy_versions_loads_as_a_case():
    row = _legacy_row("c-pg-legacy", [
        _legacy_version("事實一", "理由一", "訴願駁回。"),
        _legacy_version("", "理由二", "訴願不受理。"),
    ])

    case = PostgresStore._row_to_case(row)

    assert [v.saved_at for v in case.draft_versions] == [_SAVED_AT, _SAVED_AT]
    assert "訴願不受理。" in case.draft_versions[1].text
    assert "訴願駁回。" in case.draft_plain_text  # 舊列也沒有 draft_plain_text,讀出時補上


def test_a_postgres_row_in_the_current_shape_is_unchanged():
    row = _legacy_row("c-pg-current", [{"saved_at": _SAVED_AT, "text": "現行格式全文"}])

    case = PostgresStore._row_to_case(row)

    assert case.draft_versions[0].text == "現行格式全文"
