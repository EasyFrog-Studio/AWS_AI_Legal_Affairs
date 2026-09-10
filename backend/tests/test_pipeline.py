from datetime import date

from app.models import CaseInfo, DraftResult, LawRef, ScreeningResult, SimilarCase, StandingAssessment, Case
from app.pipeline import (
    check_deadline,
    check_deadline_from_case,
    enforce_inadmissible_format,
    guard_unsupported_clause,
    inadmissible_law_keys,
    reconcile_deadline,
    run_case,
)
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

    def assess_standing(self, info, text):
        raise AssertionError("測資無 disposition_recipient,check_standing 應回 consistent=None,不觸發 LLM")

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

    def get_law_articles(self, keys: list[str]) -> list[LawRef]:
        raise AssertionError("admissible track 不應呼叫 get_law_articles")

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


_APPEAL_ACT_77 = LawRef(
    law_name="訴願法",
    article_no="77",
    text="訴願事件有左列各款情形之一者,應為不受理之決定:…",
    amend_date="民國101年06月27日",
    source_key=None,
    relevance="條號精查",
)


class StubInadmissibleProvider(AIProvider):
    """不通過審查 -> 跳過 F2 -> 精查不受理法源 -> F3 -> F4(不受理)。"""

    def __init__(self) -> None:
        self.requested_keys: list[str] | None = None

    def extract_case_info(self, text: str) -> CaseInfo:
        return _info(case_type="社會救助")

    def screen_admissibility(self, info, text) -> ScreeningResult:
        return ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期提起訴願")

    def assess_standing(self, info, text):
        raise AssertionError("測資無 disposition_recipient,check_standing 應回 consistent=None,不觸發 LLM")

    def recommend_laws(self, info):
        raise AssertionError("inadmissible track 不應呼叫 recommend_laws(F2)")

    def get_law_articles(self, keys: list[str]) -> list[LawRef]:
        self.requested_keys = keys
        return [_APPEAL_ACT_77] + [
            _APPEAL_ACT_77.model_copy(update=dict(zip(("law_name", "article_no"), k.split("#"))))
            for k in keys[1:]
        ]

    def find_similar_cases(self, info, screening, text) -> list[SimilarCase]:
        return []

    def generate_draft(self, info, screening, laws, cases) -> DraftResult:
        # 不跑 F2 推薦,但該款次實際會引的法條都要在可引用清單裡
        assert [f"{l.law_name}#{l.article_no}" for l in laws] == [
            "訴願法#77",
            "訴願法#14",
            "行政程序法#72",
        ]
        # 故意回傳不合不受理決定書體例的草稿,驗證 enforce_inadmissible_format 會校正
        return DraftResult(
            draft_type="駁回",
            fact="模型自行補寫的事實欄",
            reason="逾期提起",
            main_text="訴願駁回。",
            cited_laws=["訴願法#77"],
        )


class StubErrorProvider(AIProvider):
    def extract_case_info(self, text: str) -> CaseInfo:
        raise RuntimeError("模擬 F1 擷取失敗")

    def screen_admissibility(self, info, text):
        raise AssertionError("不應執行到此")

    def assess_standing(self, info, text):
        raise AssertionError("不應執行到此")

    def recommend_laws(self, info):
        raise AssertionError("不應執行到此")

    def get_law_articles(self, keys):
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
    provider = StubInadmissibleProvider()

    run_case("c-22222222", store, provider)

    case = store.get("c-22222222")
    assert case.status == "done"
    assert case.current_stage == "done"
    assert case.track == "inadmissible"
    assert case.f1 is not None
    assert case.screening.passed is False
    assert case.f2 is None  # F2 被跳過,不受理法源不寫進 f2(前端據此顯示「未進行法規推薦」)
    assert provider.requested_keys == ["訴願法#77", "訴願法#14", "行政程序法#72"]  # §77(2) 逾期
    assert case.f3 == []
    assert case.f4 is not None
    assert case.f4.draft_type == "不受理"
    # 不受理決定書體例:主文為固定套語、事實欄不記載
    assert case.f4.main_text == "訴願不受理。"
    assert case.f4.fact == ""
    assert case.f4.reason == "逾期提起"


# ---------- enforce_inadmissible_format:不受理決定書體例 ----------


def _draft(**overrides) -> DraftResult:
    data = {
        "draft_type": "駁回",
        "fact": "模型自行補寫的事實欄",
        "reason": "理由內容",
        "main_text": "訴願駁回。",
        "cited_laws": [],
    }
    data.update(overrides)
    return DraftResult(**data)


def test_enforce_inadmissible_format_inadmissible_forces_fixed_main_text():
    """語料 90/90 件不受理決定書主文逐字相同,不交由模型決定。"""
    screening = ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期")

    result = enforce_inadmissible_format(_draft(main_text="本件應不予受理云云。"), screening)

    assert result.main_text == "訴願不受理。"


def test_enforce_inadmissible_format_inadmissible_clears_fact():
    """訴願法第89條第1項第3款:不受理決定得不記載事實(語料 90/90 件事實欄皆空)。"""
    screening = ScreeningResult(passed=False, matched_clause="77條第8款", reasoning="非行政處分")

    result = enforce_inadmissible_format(_draft(fact="模型自行補寫的事實欄"), screening)

    assert result.fact == ""


def test_enforce_inadmissible_format_inadmissible_coerces_draft_type():
    screening = ScreeningResult(passed=False, matched_clause="77條第6款", reasoning="處分已不存在")

    result = enforce_inadmissible_format(_draft(draft_type="原處分撤銷"), screening)

    assert result.draft_type == "不受理"


def test_enforce_inadmissible_format_inadmissible_keeps_reason():
    screening = ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期")

    result = enforce_inadmissible_format(_draft(reason="本件訴願逾法定期間。"), screening)

    assert result.reason == "本件訴願逾法定期間。"


def test_enforce_inadmissible_format_admissible_left_untouched():
    """受理案主文有多種寫法,不得套用不受理的固定套語。"""
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")
    draft = _draft(draft_type="原處分撤銷", main_text="原處分撤銷。", fact="事實欄內容")

    result = enforce_inadmissible_format(draft, screening)

    assert result == draft


def test_enforce_inadmissible_format_keeps_partial_decision_as_the_model_wrote_it():
    """部分不受理部分駁回的主文逐標的分項、事實欄要留給駁回那一部分,套固定不受理套語就錯了。"""
    screening = ScreeningResult(passed=False, matched_clause="77條第8款", reasoning="限期改善部分非行政處分")
    draft = _draft(
        draft_type="部分不受理部分駁回",
        main_text="原處分關於新臺幣12萬元罰鍰部分,訴願駁回。原處分關於限期改善部分,訴願不受理。",
        fact="罰鍰部分之事實",
    )

    result = enforce_inadmissible_format(draft, screening)

    assert result == draft


def test_enforce_inadmissible_format_still_coerces_other_admissible_types_on_inadmissible_track():
    """只有部分不受理部分駁回例外;不受理側跑出撤銷另處,一樣視為分流與草稿矛盾,照舊覆寫。"""
    screening = ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期")
    draft = _draft(draft_type="撤銷另處", main_text="原處分撤銷,由原處分機關於2個月內另為適法之處分。")

    result = enforce_inadmissible_format(draft, screening)

    assert (result.draft_type, result.main_text, result.fact) == ("不受理", "訴願不受理。", "")


def test_run_case_exception_sets_error_status():
    store = MemoryStore()
    _new_case(store, "c-33333333")

    run_case("c-33333333", store, StubErrorProvider())

    case = store.get("c-33333333")
    assert case.status == "error"
    assert case.error is not None
    assert "模擬 F1 擷取失敗" in case.error


# ---------- inadmissible_law_keys:款次 -> 可引用法條 ----------


def test_inadmissible_law_keys_overdue_includes_period_and_service_articles():
    """逾期案:語料 21 件全引訴願法§77、90% 引行政程序法§72(論證送達合法)、61% 引訴願法§14。"""
    keys = inadmissible_law_keys("77條第2款")

    assert "訴願法#77" in keys
    assert "訴願法#14" in keys
    assert "行政程序法#72" in keys


def test_inadmissible_law_keys_non_disposition_includes_article_3():
    """非行政處分案:語料 24 件全引§77,62% 引訴願法§3(行政處分定義)。"""
    keys = inadmissible_law_keys("77條第8款")

    assert keys == ["訴願法#77", "訴願法#3"]


def test_inadmissible_law_keys_disposition_gone_includes_article_1():
    keys = inadmissible_law_keys("77條第6款")

    assert keys == ["訴願法#77", "訴願法#1"]


def test_inadmissible_law_keys_clause_without_corpus_falls_back_to_77_only():
    """§77(5) 語料 0 件,不猜對應法條,只給款次本身。"""
    assert inadmissible_law_keys("77條第5款") == ["訴願法#77"]


def test_inadmissible_law_keys_clause_4_includes_corpus_derived_articles():
    """§77(4) 語料 6 件:訴願法#19、行政程序法#72 各 67%,行政程序法#74、民法#12 各 50%。"""
    keys = inadmissible_law_keys("77條第4款")

    assert keys[0] == "訴願法#77"
    assert keys == ["訴願法#77", "訴願法#19", "行政程序法#72", "行政程序法#74", "民法#12"]
    assert len(keys) == len(set(keys))


def test_inadmissible_law_keys_clause_7_has_no_article_meeting_threshold():
    """§77(7) 語料 8 件:除訴願法#77(100%)外沒有其他鍵達到 >=50% 且至少 2 件的門檻。"""
    keys = inadmissible_law_keys("77條第7款")

    assert keys == ["訴願法#77"]


def test_inadmissible_law_keys_unparsable_clause_falls_back_to_77_only():
    """判不出款次就只給§77,不猜——寧可少給也不要給錯的可引用清單。"""
    assert inadmissible_law_keys(None) == ["訴願法#77"]
    assert inadmissible_law_keys("不合法定格式") == ["訴願法#77"]


def test_inadmissible_law_keys_always_starts_with_article_77():
    """語料 90/90 件不受理案全部引訴願法§77,它是唯一 100% 的一條。"""
    for clause in [None, "77條第1款", "77條第2款", "77條第3款", "77條第6款", "77條第8款"]:
        assert inadmissible_law_keys(clause)[0] == "訴願法#77"


def test_inadmissible_law_keys_accepts_chinese_numeral_clause():
    """screening.txt 的條文本身用「一、二、…」列款,模型很可能回中文數字。"""
    assert inadmissible_law_keys("77條第二款") == inadmissible_law_keys("77條第2款")
    assert inadmissible_law_keys("77條第八款") == ["訴願法#77", "訴願法#3"]


def test_inadmissible_law_keys_ignores_other_article_clause():
    """款次屬於別條時不得誤判——「第177條第2款」不是訴願法§77第2款。"""
    assert inadmissible_law_keys("177條第2款") == ["訴願法#77"]


def test_inadmissible_law_keys_has_no_duplicates():
    for clause in [None, "77條第1款", "77條第2款", "77條第3款", "77條第6款", "77條第8款"]:
        keys = inadmissible_law_keys(clause)
        assert len(keys) == len(set(keys))


# ---------- guard_unsupported_clause:款次白名單守門(Ticket 7) ----------


def test_guard_unsupported_clause_leaves_supported_clauses_untouched():
    """語料驗證過的七款(1/2/3/4/6/7/8)照原樣通過,不動 passed 也不加 review_note。"""
    for n in (1, 2, 3, 4, 6, 7, 8):
        screening = ScreeningResult(passed=False, matched_clause=f"77條第{n}款", reasoning="理由")
        result = guard_unsupported_clause(screening)
        assert result.passed is False
        assert result.review_note == ""


def test_guard_unsupported_clause_now_supports_clauses_4_and_7():
    """§77(4)(7) 語料各有 6/8 件,不再是「本版不判」的款次。"""
    for n in (4, 7):
        screening = ScreeningResult(passed=False, matched_clause=f"77條第{n}款", reasoning="模型理由")
        result = guard_unsupported_clause(screening)
        assert result.passed is False
        assert result.review_note == ""
        assert result.reasoning == "模型理由"


def test_guard_unsupported_clause_flags_unsupported_clauses_without_flipping_passed():
    """§77(5) 語料 0 件,模型判這款時 passed 不動,只加 review_note 待人工認定。"""
    for n in (5,):
        screening = ScreeningResult(passed=False, matched_clause=f"77條第{n}款", reasoning="模型理由")
        result = guard_unsupported_clause(screening)
        assert result.passed is False  # 不逕採,但也不偷改成受理——那同樣是臆造結論
        assert f"第{n}款" in result.review_note
        assert "須人工認定" in result.review_note
        assert result.reasoning == "模型理由"  # 原理由保留,只加註記


def test_guard_unsupported_clause_flags_unparsable_clause():
    screening = ScreeningResult(passed=False, matched_clause="不合法定格式", reasoning="理由")
    result = guard_unsupported_clause(screening)
    assert result.review_note != ""
    assert "須人工認定" in result.review_note


def test_guard_unsupported_clause_ignores_other_article_clause():
    """「第177條第2款」不是訴願法§77第2款,款次解析會回 None,同樣待人工認定。"""
    screening = ScreeningResult(passed=False, matched_clause="177條第2款", reasoning="理由")
    result = guard_unsupported_clause(screening)
    assert result.review_note != ""


def test_guard_unsupported_clause_does_not_touch_admissible_screening():
    """passed=True(受理)不受此守門影響,款次守門只管不受理分支。"""
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")
    result = guard_unsupported_clause(screening)
    assert result == screening


def test_run_case_flags_unsupported_clause_but_still_produces_a_draft():
    """守門不擋流程——即使款次未受語料支撐,F3/F4 仍要跑完,只是 screening 帶著 review_note。"""
    store = MemoryStore()
    _new_case(store, "c-55555555")

    class _UnsupportedClauseProvider(StubInadmissibleProvider):
        def screen_admissibility(self, info, text) -> ScreeningResult:
            return ScreeningResult(passed=False, matched_clause="77條第5款", reasoning="模型判第5款")

        def generate_draft(self, info, screening, laws, cases) -> DraftResult:
            # §77(5) 語料 0 件,inadmissible_law_keys 只給訴願法#77,不驗證清單內容,只確保流程能跑完
            return DraftResult(
                draft_type="不受理", fact="", reason="模型判第5款", main_text="訴願不受理。"
            )

    run_case("c-55555555", store, _UnsupportedClauseProvider())

    case = store.get("c-55555555")
    assert case.status == "done"
    assert case.screening.matched_clause == "77條第5款"
    assert "須人工認定" in case.screening.review_note
    assert case.f4 is not None


# ---------- run_case 串接§77(1)自動判(Ticket 5) ----------


def test_run_case_auto_overrides_to_77_1_when_appellant_and_agency_both_missing():
    """F1 擷取結果姓名與機關皆缺,run_case 應自動覆寫為第1款不受理,不需模型自己判出來。"""
    store = MemoryStore()
    _new_case(store, "c-66666666")

    class _MissingIdentityProvider(AIProvider):
        def extract_case_info(self, text):
            return CaseInfo(
                appellant="",
                agency="",
                disposition_date="110年3月5日",
                disposition_no="彰環廢字第1號",
                disposition_summary="裁處罰鍰",
                case_type="廢棄物清理",
                appeal_reasons=["原處分認定事實有誤"],
                receipt_date="110年3月10日",
            )

        def screen_admissibility(self, info, text):
            return ScreeningResult(passed=True, matched_clause=None, reasoning="模型未檢出缺漏")

        def assess_standing(self, info, text):
            raise AssertionError("測資無 disposition_recipient,不應觸發 LLM")

        def recommend_laws(self, info):
            raise AssertionError("§77(1)不受理應跳過F2")

        def get_law_articles(self, keys):
            return [_APPEAL_ACT_77]

        def find_similar_cases(self, info, screening, text):
            return []

        def generate_draft(self, info, screening, laws, cases):
            return DraftResult(draft_type="不受理", fact="", reason="缺漏不能補正", main_text="訴願不受理。")

    run_case("c-66666666", store, _MissingIdentityProvider())

    case = store.get("c-66666666")
    assert case.status == "done"
    assert case.screening.passed is False
    assert case.screening.matched_clause == "77條第1款"
    assert case.screening.review_note != ""
    assert case.track == "inadmissible"


# ---------- run_case 串接§77(3)自動判(Ticket 6) ----------


def test_run_case_calls_assess_standing_only_when_recipient_inconsistent():
    """相對人與訴願人不一致才呼叫 provider.assess_standing——一致或欄位空白都不該打這支 LLM。"""
    store = MemoryStore()
    _new_case(store, "c-77777777")
    calls = []

    class _InconsistentRecipientProvider(AIProvider):
        def extract_case_info(self, text):
            return CaseInfo(
                appellant="王大明",
                agency="彰化縣環境保護局",
                disposition_date="110年3月5日",
                disposition_no="彰環廢字第1號",
                disposition_summary="裁處罰鍰",
                case_type="廢棄物清理",
                disposition_recipient="李小華",  # 與 appellant 不同
            )

        def screen_admissibility(self, info, text):
            return ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")

        def assess_standing(self, info, text):
            calls.append((info.appellant, info.disposition_recipient))
            return StandingAssessment(referenced_norm="廢棄物清理法#27", has_standing=False)  # 無利害關係

        def recommend_laws(self, info):
            raise AssertionError("§77(3)不受理應跳過F2")

        def get_law_articles(self, keys):
            return [_APPEAL_ACT_77]

        def find_similar_cases(self, info, screening, text):
            return []

        def generate_draft(self, info, screening, laws, cases):
            return DraftResult(draft_type="不受理", fact="", reason="無利害關係", main_text="訴願不受理。")

    run_case("c-77777777", store, _InconsistentRecipientProvider())

    assert calls == [("王大明", "李小華")]
    case = store.get("c-77777777")
    assert case.screening.passed is False
    assert case.screening.matched_clause == "77條第3款"
    assert case.screening.review_note != ""


def test_run_case_does_not_override_when_model_cites_no_protective_norm():
    """模型指不出具體保護規範(referenced_norm 空)時,即使 has_standing=False,
    也不得覆寫為第3款不受理——這是實作計畫§Ticket 6 約束2 的端到端驗證。"""
    store = MemoryStore()
    _new_case(store, "c-88888888")

    class _NoNormCitedProvider(AIProvider):
        def extract_case_info(self, text):
            return CaseInfo(
                appellant="王大明",
                agency="彰化縣環境保護局",
                disposition_date="110年3月5日",
                disposition_no="彰環廢字第1號",
                disposition_summary="裁處罰鍰",
                case_type="廢棄物清理",
                disposition_recipient="李小華",
            )

        def screen_admissibility(self, info, text):
            return ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")

        def assess_standing(self, info, text):
            # 模型給了 has_standing=False,但指不出具體法規名稱＋條號
            return StandingAssessment(referenced_norm="", has_standing=False)

        def recommend_laws(self, info):
            return [
                LawRef(
                    law_name="廢棄物清理法",
                    article_no="27",
                    text="條文",
                    amend_date="民國106年01月18日",
                    source_key=None,
                    relevance="相關",
                )
            ]

        def get_law_articles(self, keys):
            raise AssertionError("受理分支不應呼叫 get_law_articles")

        def find_similar_cases(self, info, screening, text):
            return []

        def generate_draft(self, info, screening, laws, cases):
            return DraftResult(draft_type="駁回", fact="事實", reason="理由", main_text="訴願駁回。")

    run_case("c-88888888", store, _NoNormCitedProvider())

    case = store.get("c-88888888")
    assert case.screening.passed is True  # 未被覆寫
    assert case.screening.review_note != ""  # 但須標記待人工認定


def test_run_case_inadmissible_passes_all_clause_laws_into_allowed_list():
    """§77(1) 是最長的一列(4 條附加),驗證整列都進得了草稿的可引用清單。"""
    store = MemoryStore()
    _new_case(store, "c-44444444")
    seen: dict = {}

    class _Stub(StubInadmissibleProvider):
        def screen_admissibility(self, info, text) -> ScreeningResult:
            return ScreeningResult(passed=False, matched_clause="77條第1款", reasoning="逾期未補正")

        def generate_draft(self, info, screening, laws, cases) -> DraftResult:
            seen["allowed"] = [f"{l.law_name}#{l.article_no}" for l in laws]
            return DraftResult(
                draft_type="不受理", fact="", reason="逾期未補正", main_text="訴願不受理。"
            )

    run_case("c-44444444", store, _Stub())

    assert seen["allowed"] == [
        "訴願法#77",
        "訴願法#56",
        "訴願法#47",
        "行政訴訟法#67",
        "行政訴訟法#71",
    ]


# ---------- check_deadline / reconcile_deadline:期間認定接進程序審查 ----------


_OVERDUE_TEXT = (
    "系爭裁處書於114年5月28日送達訴願人戶籍地,已生合法送達效力。"
    "訴願人住居所位於本市,無須扣除在途期間。"
    "訴願人遲至114年10月31日始提起訴願。"
)


# 完整教示條款(法定 30 日):不放這句的原處分書會被 Ticket 8 判為未告知救濟期間而改算一年,
# 那是正確行為,但會蓋掉本組測試真正要驗的分槽讀法,故 fixture 一律附上完整教示。
_FULL_NOTICE_CLAUSE = "如不服本處分,得於本處分書送達之次日起三十日內,繕具訴願書向本府提起訴願。"


def _overdue_documents() -> dict:
    """run_case 走真實 pipeline 時的分槽版本:送達日搬進 service 槽用官方欄位名,
    其餘(住居所、收文日)留在 appeal 槽——對應 check_deadline_from_case 的分槽讀法。"""
    from app.models import CaseDocument, DocumentCheck

    return {
        "appeal": CaseDocument(
            slot="appeal", source="text", text=_OVERDUE_TEXT,
            check=DocumentCheck(matched=True, method="rule", note="測試用"),
        ),
        "service": CaseDocument(
            slot="service", source="text", text="送達時間:中華民國114年5月28日。送達方式:寄存於派出所。",
            check=DocumentCheck(matched=True, method="rule", note="測試用"),
        ),
        "disposition": CaseDocument(
            slot="disposition", source="text", text=f"主旨:裁處罰鍰。{_FULL_NOTICE_CLAUSE}",
            check=DocumentCheck(matched=True, method="rule", note="測試用"),
        ),
    }


def test_check_deadline_reads_dates_from_the_case_file():
    check = check_deadline(_OVERDUE_TEXT)

    assert check.overdue is True
    assert check.due_date == date(2025, 6, 27)
    assert "114年6月27日" in check.detail  # 算式以民國紀年書寫,決定書理由可直接引用
    assert check.review_note == ""  # 逾期 126 日,遠超連假長度,不受未接國定假日表影響


def test_check_deadline_without_dates_reports_why_instead_of_passing():
    """訴願書未載送達日是常態,此時要說「沒算」,不能當成未逾期。"""
    check = check_deadline("訴願人不服原處分,請求撤銷。")

    assert check.overdue is None
    assert "送達日" in check.review_note


def test_check_deadline_shifts_the_due_date_over_a_national_holiday():
    """末日 114年5月30日為端午調整放假,應順延至次一上班日 6月2日(星期一)。"""
    text = (
        "系爭處分書於114年4月30日送達訴願人營業所。訴願人住居所位於本市,無須扣除在途期間。"
        "訴願人遲至114年8月1日始提起訴願。"
    )

    check = check_deadline(text)

    assert check.due_date == date(2025, 6, 2)
    assert check.review_note == ""


def test_check_deadline_flags_years_the_holiday_table_does_not_cover():
    """假日表只收到民國116年,超出範圍就不能當成該年沒有假日。"""
    text = (
        "系爭處分書於120年5月28日送達訴願人營業所。訴願人住居所位於本市,無須扣除在途期間。"
        "訴願人遲至120年10月31日始提起訴願。"
    )

    check = check_deadline(text)

    assert check.overdue is True
    assert "國定假日未收錄" in check.review_note


def test_check_deadline_falls_back_to_the_transit_table():
    """卷內未載在途期間時,以住居所查訴願扣除在途期間辦法附表:桃園市->新北市為 3 日。"""
    text = (
        "訴願人\n住居所：桃園市中壢區中央西路二段100號\n"
        "訴願人於114年1月15日收受系爭裁處書。訴願人遲至114年10月23日始提起訴願。"
    )

    check = check_deadline(text)

    assert "在途3日" in check.detail
    assert check.due_date == date(2025, 2, 17)  # 與語料 1141011503 機關自己算的末日相同


_MASKED_RESIDENCE_TEXT = (
    "訴願人\n住居所：臺中市○○區中山路1號\n"
    "訴願人於114年1月15日收受系爭裁處書。訴願人遲至114年10月23日始提起訴願。"
)


def test_check_deadline_computes_with_a_masked_district_when_both_groups_agree():
    """住居所被遮罩成「臺中市○○區」不必整筆棄權:附表臺中市(一)(二)對新北市都是 5 日,
    之分一天都不差(見實作計畫 Ticket 13)。"""
    check = check_deadline(_MASKED_RESIDENCE_TEXT)

    assert "在途5日" in check.detail
    assert check.overdue is True


def test_check_deadline_declines_with_a_masked_district_when_groups_differ(monkeypatch):
    """受理機關換成臺北市時,高雄市(一)5 日、(二)6 日不同,仍不算——但要講出差幾日,
    不能籠統說「無法由住居所認定」,那會讓補一個行政區就能救的案子看起來也沒救。"""
    from app.config import settings

    monkeypatch.setattr(settings, "APPEAL_AGENCY_LOCATION", "臺北市")
    text = _MASKED_RESIDENCE_TEXT.replace("臺中市○○區中山路1號", "高雄市○○區三多路3號")

    check = check_deadline(text)

    assert check.due_date is None
    assert "高雄市" in check.review_note
    assert "相差1日" in check.review_note


def test_reconcile_deadline_computed_overdue_overrides_the_model():
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")

    result, check = reconcile_deadline(screening, check_deadline(_OVERDUE_TEXT))

    assert result.passed is False
    assert result.matched_clause == "77條第2款"
    assert check.detail in result.reasoning  # 算式進理由,F4 才寫得出機關自己的算法
    assert "無不受理事由" in result.reasoning  # 原程序審查意見一併保留


def test_reconcile_deadline_records_conflict_instead_of_flipping_silently():
    """算出未逾期而模型仍指第2款:兩邊都不動,把歧異寫下來送人工。"""
    timely = _OVERDUE_TEXT.replace("114年10月31日", "114年6月20日")
    screening = ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="模型認為逾期")

    result, check = reconcile_deadline(screening, check_deadline(timely))

    assert check.overdue is False
    assert result.matched_clause == "77條第2款"  # 未偷改模型結論
    assert "不符" in check.review_note


def test_dates_from_an_ocr_slot_never_override_the_screening():
    """經 OCR 取得文字的槽,日期一律不得據以覆寫程序審查:模型抽字會編字,而這套系統的
    正確性建立在日期上(見實作計畫 Ticket 4)。算得出逾期也只能標待人工。"""
    documents = _overdue_documents()
    documents["service"] = documents["service"].model_copy(
        update={"ocr": True, "review_note": "本槽文字由 OCR 取得,日期須人工核對原件"}
    )
    case = Case(
        case_id="c-ocr00001",
        created_at="2026-08-17T00:00:00",
        title="掃描件案",
        source="pdf",
        input_text=_OVERDUE_TEXT,
        documents=documents,
    )

    check = check_deadline_from_case(case)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")
    result, reconciled = reconcile_deadline(screening, check)

    assert check.overdue is True  # 算式本身照算,不是不算
    assert "OCR" in check.review_note
    assert result.passed is True  # 但不覆寫程序審查
    assert "未據以覆寫程序審查" in reconciled.review_note


def test_run_case_stores_the_deadline_check():
    """run_case 走真實 pipeline,期間計算讀 case.documents(分槽),不是 input_text 合併字串——
    service_date 只信 service 槽,故送達日的敘述要放在 service 槽,不是隨便塞進 appeal 槽。"""
    store = MemoryStore()
    case = Case(
        case_id="c-33333333",
        created_at="2026-08-17T00:00:00",
        title="逾期案件",
        source="text",
        input_text=_OVERDUE_TEXT,
        documents=_overdue_documents(),
    )
    store.create(case)

    run_case("c-33333333", store, StubInadmissibleProvider())

    stored = store.get("c-33333333")
    assert stored.deadline.overdue is True
    assert stored.deadline.due_date == date(2025, 6, 27)
    assert stored.screening.matched_clause == "77條第2款"


def test_reconcile_deadline_does_not_override_while_the_note_stands():
    """算式自己還要人工確認(此例為公示送達),就不得拿去覆寫審查結果或寫進草稿。"""
    text = (
        "系爭處分書依法辦理公示送達,於114年2月2日公告張貼於本府公告欄。"
        "訴願人住居所位於本市,無須扣除在途期間。訴願人遲至114年8月6日始提起訴願。"
    )
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")

    result, check = reconcile_deadline(screening, check_deadline(text))

    assert check.overdue is True
    assert result.passed is True  # 未覆寫
    assert result.matched_clause is None
    assert "未據以覆寫程序審查" in check.review_note

# --- 教示條款 -> 行政程序法§98 三分支(Ticket 8)---------------------------------
# 共同前提:送達生效日 114年5月28日、機關收文日 114年10月31日、在途 0 日。
# 依訴願法§14 的 30 日算末日為 114年6月27日 -> 已逾期;三分支各自把這個結論推翻或懸置。


def _case_with_disposition(disposition_text: str, appeal_text: str = _OVERDUE_TEXT) -> Case:
    from app.models import CaseDocument

    return Case(
        case_id="c-98",
        created_at="2026-09-09T00:00:00Z",
        title="教示條款測試",
        source="text",
        input_text="",
        documents={
            "appeal": CaseDocument(slot="appeal", source="text", text=appeal_text),
            "service": CaseDocument(slot="service", source="text", text="送達時間:中華民國114年5月28日。"),
            "disposition": CaseDocument(slot="disposition", source="text", text=disposition_text),
        },
    )


def test_full_notice_clause_still_computes_the_thirty_day_period():
    """有完整教示的案件行為不變:仍走 30 日、仍算得出逾期、不留待人工記號。"""
    check = check_deadline_from_case(_case_with_disposition(f"主旨:裁處罰鍰。{_FULL_NOTICE_CLAUSE}"))

    assert check.overdue is True
    assert check.due_date == date(2025, 6, 27)
    assert check.review_note == ""


def test_disposition_without_a_notice_clause_uses_the_one_year_period():
    """§98 III:未教示案不得被當一般 30 日案算——誤判方向不利人民,這是 Ticket 8 的主症狀。"""
    check = check_deadline_from_case(_case_with_disposition("主旨:裁處罰鍰。"))

    assert check.due_date == date(2026, 5, 28)  # 起算日 114年5月29日 -> 一年後前一日
    assert check.overdue is False  # 收文日 114年10月31日在一年內
    assert "第98條第3項" in check.review_note
    assert "未告知救濟期間" in check.detail


def test_longer_stated_period_uses_the_stated_period():
    """§98 II:告知 60 日者依原告知期間算,末日晚於法定 30 日的 114年6月27日。"""
    check = check_deadline_from_case(
        _case_with_disposition("主旨:裁處罰鍰。如不服本處分,得於送達次日起六十日內提起訴願。")
    )

    assert check.due_date == date(2025, 7, 28)  # 原末日 7月27日為星期日,順延至次一上班日
    assert "期間60日" in check.detail
    assert "第98條第2項" in check.review_note
    # 連告知的較長期間都逾越:§98 II 的信賴保護不適用,但仍可能落入同條第3項的一年
    assert "第98條第2項不適用" in check.review_note


def test_corrected_notice_clause_restarts_from_the_correction_notice():
    """§98 I:告知錯誤已通知更正 -> 自更正通知送達之翌日起算法定 30 日。"""
    appeal = _OVERDUE_TEXT + "原處分機關就救濟期間錯誤另以更正通知於114年6月10日送達訴願人。"
    check = check_deadline_from_case(
        _case_with_disposition(
            "主旨:裁處罰鍰。如不服本處分,得於送達次日起二十日內提起訴願。", appeal_text=appeal
        )
    )

    assert check.due_date == date(2025, 7, 10)  # 起算日 114年6月11日 + 30 日
    assert "更正通知送達日114年6月10日" in check.detail
    assert "第98條第1項" in check.review_note


def test_wrong_notice_clause_without_correction_record_computes_no_deadline():
    """有無通知更正抽不到時不給末日、不給逾期結論——30 日算式在此方向不利人民。"""
    check = check_deadline_from_case(
        _case_with_disposition("主旨:裁處罰鍰。如不服本處分,得於送達次日起二十日內提起訴願。")
    )

    assert check.overdue is None
    assert check.due_date is None
    assert check.service_date == date(2025, 5, 28)  # 送達生效日仍交出去,承辦人接手算
    assert "不得逕認未為更正" in check.review_note


def test_no_disposition_slot_reports_that_the_notice_clause_was_not_checked():
    """卷內無原處分書:期間照 30 日算(維持既有行為),但要說出沒檢核過教示,不可靜默放行。"""
    from app.models import CaseDocument

    case = Case(
        case_id="c-98b",
        created_at="2026-09-09T00:00:00Z",
        title="無原處分書",
        source="text",
        input_text="",
        documents={
            "appeal": CaseDocument(slot="appeal", source="text", text=_OVERDUE_TEXT),
            "service": CaseDocument(slot="service", source="text", text="送達時間:中華民國114年5月28日。"),
        },
    )

    check = check_deadline_from_case(case)

    assert check.due_date == date(2025, 6, 27)
    assert "有無教示救濟期間未經檢核" in check.review_note


def test_article_98_branches_never_override_the_screening_result():
    """三分支都是語料零件,一律不覆寫程序審查——即使算出逾期也只留歧異記錄。"""
    dispositions = [
        "主旨:裁處罰鍰。",  # §98 III 一年
        "主旨:裁處罰鍰。如不服本處分,得於送達次日起六十日內提起訴願。",  # §98 II
        "主旨:裁處罰鍰。如不服本處分,得於送達次日起二十日內提起訴願。",  # 分支未定
    ]
    for text in dispositions:
        screening = ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")

        result, check = reconcile_deadline(screening, check_deadline_from_case(_case_with_disposition(text)))

        assert result.passed is True, text
        assert result.matched_clause is None, text
        assert check.review_note != "", text


def test_run_case_applies_the_notice_clause_from_this_round_of_f1():
    """§98 檢核吃的是本輪 F1 的 disposition_notice_clause;run_case 手上的 case 快照
    f1 還是 None,漏傳 info 就會整段跳過檢核而看不出來。"""
    store = MemoryStore()
    store.create(_case_with_disposition("主旨:裁處罰鍰。"))  # 原處分書無教示條款

    run_case("c-98", store, StubInadmissibleProvider())

    stored = store.get("c-98")
    assert stored.deadline.due_date == date(2026, 5, 28)  # 走一年,不是 30 日
    # 模型判第2款而算出未逾期,歧異記錄與§98 的期間認定要並存——後者才解釋得出歧異從何而來
    assert "第98條第3項" in stored.deadline.review_note
    assert "與程序審查認定之第2款不符" in stored.deadline.review_note
    assert stored.screening.matched_clause == "77條第2款"  # 未偷改模型結論
