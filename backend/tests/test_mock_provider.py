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
