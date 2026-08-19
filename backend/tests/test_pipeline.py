from app.models import CaseInfo, DraftResult, LawRef, ScreeningResult, SimilarCase, Case
from app.pipeline import run_case
from app.providers.base import AIProvider
from app.store import MemoryStore


def _info(case_type="廢棄物清理"):
    return CaseInfo(
        appellant="王大明",
        agency="彰化縣環境保護局",
        disposition_date="110年3月5日",
        disposition_no="彰環廢字第1號",
        disposition_summary="裁處罰鍰",
        case_type=case_type,
    )


class StubAdmissibleProvider(AIProvider):
    """通過審查 -> F2 -> F3 -> F4(駁回)。"""

    def extract_case_info(self, text: str) -> CaseInfo:
        return _info()

    def screen_admissibility(self, info: CaseInfo, text: str) -> ScreeningResult:
        return ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")

    def recommend_laws(self, info: CaseInfo) -> list[LawRef]:
        return [
            LawRef(
                law_name="廢棄物清理法",
                article_no="27",
                text="條文內容",
                amend_date="民國106年01月18日",
                source_key=None,
                relevance="相關",
            )
        ]

    def find_similar_cases(self, info, screening, text) -> list[SimilarCase]:
        return [
            SimilarCase(
                case_no="彰府訴字第1號",
                year="109",
                case_type="廢棄物清理",
                appeal_article="無",
                issue="任意棄置",
                result="駁回",
                summary="摘要",
                similarity_note="相似",
                source_key=None,
            )
        ]

    def generate_draft(self, info, screening, laws, cases) -> DraftResult:
        assert laws  # F2 結果應傳入
        return DraftResult(
            draft_type="駁回", fact="事實", reason="理由", main_text="訴願駁回。", cited_laws=["廢棄物清理法#27"]
        )


class StubInadmissibleProvider(AIProvider):
    """不通過審查 -> 跳過 F2 -> F3 -> F4(不受理)。"""

    def extract_case_info(self, text: str) -> CaseInfo:
        return _info(case_type="社會救助")

    def screen_admissibility(self, info, text) -> ScreeningResult:
        return ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期提起訴願")

    def recommend_laws(self, info):
        raise AssertionError("inadmissible track 不應呼叫 recommend_laws(F2)")

    def find_similar_cases(self, info, screening, text) -> list[SimilarCase]:
        return []

    def generate_draft(self, info, screening, laws, cases) -> DraftResult:
        assert laws == []  # 跳過 F2,laws 應為空
        return DraftResult(
            draft_type="不受理", fact="事實", reason="逾期提起", main_text="訴願不受理。", cited_laws=["訴願法#77"]
        )


class StubErrorProvider(AIProvider):
    def extract_case_info(self, text: str) -> CaseInfo:
        raise RuntimeError("模擬 F1 擷取失敗")

    def screen_admissibility(self, info, text):
        raise AssertionError("不應執行到此")

    def recommend_laws(self, info):
        raise AssertionError("不應執行到此")

    def find_similar_cases(self, info, screening, text):
        raise AssertionError("不應執行到此")

    def generate_draft(self, info, screening, laws, cases):
        raise AssertionError("不應執行到此")


def _new_case(store: MemoryStore, case_id="c-11111111") -> Case:
    case = Case(
        case_id=case_id,
        created_at="2026-08-17T00:00:00",
        title="測試案件",
        source="text",
        input_text="訴願書原文內容",
    )
    store.create(case)
    return case


def test_run_case_admissible_track_progresses_through_all_stages():
    store = MemoryStore()
    _new_case(store)

    run_case("c-11111111", store, StubAdmissibleProvider())

    case = store.get("c-11111111")
    assert case.status == "done"
    assert case.current_stage == "done"
    assert case.track == "admissible"
    assert case.f1 is not None
    assert case.screening.passed is True
    assert case.f2 is not None and len(case.f2) == 1
    assert case.f3 is not None and len(case.f3) == 1
    assert case.f4 is not None
    assert case.f4.draft_type == "駁回"
    assert case.error is None


def test_run_case_inadmissible_track_skips_f2():
    store = MemoryStore()
    _new_case(store, "c-22222222")

    run_case("c-22222222", store, StubInadmissibleProvider())

    case = store.get("c-22222222")
    assert case.status == "done"
    assert case.current_stage == "done"
    assert case.track == "inadmissible"
    assert case.f1 is not None
    assert case.screening.passed is False
    assert case.f2 is None  # F2 被跳過
    assert case.f3 == []
    assert case.f4 is not None
    assert case.f4.draft_type == "不受理"


def test_run_case_exception_sets_error_status():
    store = MemoryStore()
    _new_case(store, "c-33333333")

    run_case("c-33333333", store, StubErrorProvider())

    case = store.get("c-33333333")
    assert case.status == "error"
    assert case.error is not None
    assert "模擬 F1 擷取失敗" in case.error
