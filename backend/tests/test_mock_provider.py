from pathlib import Path

from app.models import CaseInfo, ScreeningResult
from app.providers.mock import MockProvider

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _provider() -> MockProvider:
    return MockProvider(data_dir=str(FIXTURES_DIR))


def test_extract_case_info_matches_sample_a_by_keywords():
    provider = _provider()
    text = "訴願人王大明不服彰化縣環境保護局裁處罰鍰,提起廢棄物清理訴願。"
    info = provider.extract_case_info(text)
    assert isinstance(info, CaseInfo)
    assert info.appellant == "王大明"
    assert info.agency == "彰化縣環境保護局"


def test_extract_case_info_matches_sample_b_by_keywords():
    provider = _provider()
    text = "訴願人李小華對臺北市政府社會局不服,逾期提起社會救助訴願。"
    info = provider.extract_case_info(text)
    assert info.appellant == "李小華"
    assert info.case_type == "社會救助"


def test_extract_case_info_unmatched_text_falls_back():
    provider = _provider()
    info = provider.extract_case_info("完全無關的隨機輸入文字,不含任何樣本關鍵詞")
    assert info.case_type == "未分類"


def test_screen_admissibility_uses_matched_sample():
    provider = _provider()
    text = "訴願人王大明不服彰化縣環境保護局裁處罰鍰,提起廢棄物清理訴願。"
    info = provider.extract_case_info(text)
    result = provider.screen_admissibility(info, text)
    assert isinstance(result, ScreeningResult)
    assert result.passed is True


def test_recommend_laws_matches_by_info_after_extract():
    provider = _provider()
    text = "訴願人王大明不服彰化縣環境保護局裁處罰鍰,提起廢棄物清理訴願。"
    info = provider.extract_case_info(text)
    laws = provider.recommend_laws(info)
    assert len(laws) == 2
    assert laws[0].law_name == "廢棄物清理法"


def test_assess_standing_returns_none_when_sample_has_no_standing_key():
    """既有樣本沒有一件是§77(3)當事人適格案,沒有 "standing" 鍵時回空 StandingAssessment
    (referenced_norm="",has_standing=None),不是預設有或無利害關係。"""
    provider = _provider()
    text = "訴願人王大明不服彰化縣環境保護局裁處罰鍰,提起廢棄物清理訴願。"
    info = provider.extract_case_info(text)
    result = provider.assess_standing(info, text)
    assert result.referenced_norm == ""
    assert result.has_standing is None


def test_assess_standing_parses_structured_fields_when_sample_has_standing_key(tmp_path):
    """樣本一旦補上 "standing" 鍵,MockProvider 要把 referenced_norm/has_standing 原樣傳出。"""
    import json
    import shutil

    for name in ("sample_a.json", "_fallback.json"):
        shutil.copy(FIXTURES_DIR / name, tmp_path / name)
    sample = json.loads((tmp_path / "sample_a.json").read_text(encoding="utf-8"))
    sample["expected"]["standing"] = {"referenced_norm": "廢棄物清理法#27", "has_standing": False}
    (tmp_path / "sample_a.json").write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

    provider = MockProvider(data_dir=str(tmp_path))
    text = "訴願人王大明不服彰化縣環境保護局裁處罰鍰,提起廢棄物清理訴願。"
    result = provider.assess_standing(provider.extract_case_info(text), text)

    assert result.referenced_norm == "廢棄物清理法#27"
    assert result.has_standing is False


def test_generate_draft_matches_by_info_for_inadmissible_sample():
    provider = _provider()
    text = "訴願人李小華對臺北市政府社會局不服,逾期提起社會救助訴願。"
    info = provider.extract_case_info(text)
    screening = provider.screen_admissibility(info, text)
    draft = provider.generate_draft(info, screening, [], [])
    assert draft.draft_type == "不受理"


def test_find_similar_cases_fallback_when_unmatched():
    provider = _provider()
    text = "無關輸入完全比對不到任何樣本"
    info = provider.extract_case_info(text)
    screening = provider.screen_admissibility(info, text)
    cases = provider.find_similar_cases(info, screening, text)
    assert cases == []


def test_get_law_articles_returns_ref_from_samples():
    provider = _provider()
    refs = provider.get_law_articles(["廢棄物清理法#27"])

    assert len(refs) == 1
    assert refs[0].law_name == "廢棄物清理法"
    assert refs[0].article_no == "27"


def test_get_law_articles_unknown_key_falls_back_to_placeholder():
    provider = _provider()
    refs = provider.get_law_articles(["訴願法#77"])

    assert len(refs) == 1
    assert refs[0].law_name == "訴願法"
    assert refs[0].article_no == "77"
    assert refs[0].amend_date == "未收錄"  # 比照真實 provider,查無不得省略該筆


def test_recommend_laws_is_capped_at_three_like_the_real_providers():
    """出貨用的 data_show 樣本每件手寫五條;不在這裡截斷,mock 模式看到的筆數就與真實模式不同。
    刻意讀真正出貨的那份樣本而不是 tests/fixtures——fixtures 只有兩條,截不截斷都會過。"""
    shipped = Path(__file__).resolve().parents[2] / "data_show" / "sample_appeals"
    provider = MockProvider(data_dir=str(shipped))
    # d_dismiss_waste 樣本手寫五條;三個欄位都對上才會命中它而不是退到 _fallback
    info = CaseInfo(
        appellant="陳○瑤",
        agency="新北市政府環境保護局",
        case_type="廢棄物清理法",
        disposition_date="112年1月10日",
        disposition_no="新北環稽字第1號",
        disposition_summary="裁處罰鍰",
    )

    assert len(provider.recommend_laws(info)) <= 3


# ---------- F2+ 參考見解 ----------


def test_find_references_matches_sample_by_info():
    provider = _provider()
    text = "訴願人王大明不服彰化縣環境保護局裁處罰鍰,提起廢棄物清理訴願。"
    info = provider.extract_case_info(text)

    refs = provider.find_references(info)

    assert [r.doc_kind for r in refs] == ["行政法院裁判", "行政函釋"]
    assert refs[0].name == "最高行政法院 102年度判字第147號"
    assert refs[1].issuer == "內政部"
    assert refs[1].topic == ""  # 函釋樣本無題旨,與釋字樣本形狀不同


def test_find_references_second_sample_has_a_different_shape():
    provider = _provider()
    text = "訴願人李小華對臺北市政府社會局不服,逾期提起社會救助訴願。"
    info = provider.extract_case_info(text)

    (ref,) = provider.find_references(info)

    assert ref.doc_kind == "司法院釋字"
    assert ref.issuer == ""  # 釋字無發文機關
    assert ref.issued_date == "未收錄"
    assert ref.source_key is None


def test_find_references_caps_at_three():
    provider = _provider()
    info = provider.extract_case_info("完全無關的隨機輸入文字,不含任何樣本關鍵詞")

    assert len(provider.find_references(info)) == 3


def test_find_references_raises_when_sample_lacks_the_key(tmp_path):
    """樣本缺鍵要大聲壞掉,不能靜默回空清單——那會與「檢索後無結果」混為一談。"""
    import json
    import shutil

    for name in ("_fallback.json", "sample_a.json"):
        data = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
        data["expected"].pop("f2_refs", None)
        (tmp_path / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    provider = MockProvider(data_dir=str(tmp_path))
    info = provider.extract_case_info("完全無關的隨機輸入文字")
    try:
        provider.find_references(info)
    except KeyError:
        return
    raise AssertionError("樣本缺 f2_refs 應該拋錯,而不是回空清單")


# ---------- 全欄位擴充:mock 樣本涵蓋 CaseInfo 全部欄位、日期已是標準寫法、F4 有 gist ----------

import json as _json

from app.models import CASE_INFO_DATE_FIELDS
from app.dates import normalize_roc

_SHIPPED_DIR = Path(__file__).resolve().parents[2] / "data_show" / "sample_appeals"


def _all_sample_files() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("*.json")) + sorted(_SHIPPED_DIR.glob("*.json"))


def test_every_sample_f1_has_all_case_info_fields():
    for path in _all_sample_files():
        sample = _json.loads(path.read_text(encoding="utf-8"))
        f1 = sample["expected"]["f1"]
        missing = set(CaseInfo.model_fields.keys()) - set(f1.keys())
        assert not missing, f"{path.name} 缺欄位: {missing}"


def test_every_sample_date_field_already_uses_the_standard_roc_writing():
    for path in _all_sample_files():
        sample = _json.loads(path.read_text(encoding="utf-8"))
        f1 = sample["expected"]["f1"]
        for field in CASE_INFO_DATE_FIELDS:
            value = f1.get(field, "")
            if not value:
                continue
            assert normalize_roc(value) == value, f"{path.name}.{field} = {value!r} 不是標準寫法"


def test_every_sample_f4_has_a_non_empty_gist():
    for path in _all_sample_files():
        sample = _json.loads(path.read_text(encoding="utf-8"))
        f4 = sample["expected"].get("f4")
        if f4 is None:
            continue
        assert f4.get("gist"), f"{path.name} 的 f4 缺 gist"
