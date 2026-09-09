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
