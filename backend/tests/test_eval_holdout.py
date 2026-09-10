"""留出測試集評測工具(tools/eval_holdout.py)的單元測試,不需 PDF、不需外部服務。"""
import json

import fitz
import pytest

from app.models import Case, CaseInfo, DraftResult, LawRef, ScreeningResult, SimilarCase
from app.pipeline import run_case
from app.providers.base import AIProvider
from app.store import MemoryStore
from tools.eval_holdout import aggregate, evaluate_case, extract_law_keys, run

# ---------- extract_law_keys ----------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("依訴願法第 1 9 條規定應予駁回", ["訴願法#19"]),  # 排版拆開的空白須先去除
        ("查訴願法第七十七條之規定", ["訴願法#77"]),  # 中文數字
        ("行政程序法第98條之一準用之", ["行政程序法#98-1"]),  # 「之一」子項
        ("已違反廢棄物清理法第27條,同法第50條規定", ["廢棄物清理法#27", "廢棄物清理法#50"]),  # 「同法」回溯前一法規名
        ("次按違反社會救助法第5條規定", ["社會救助法#5"]),  # 前綴可疊加剝除
        ("本件事實已如前述,並無不受理事由", []),  # 無法規名文字回空清單
        ("訴願法第77條,訴願法第77條均定有明文", ["訴願法#77"]),  # 去重保序
        ("該法第10條規定", []),  # 回溯不到明確法規名時整筆丟棄
        # 前一條文與「同法」之間無標點:動詞類前綴出現在名稱中段,須從最後一個切,不得吞成「送達不能依同法」
        ("行政程序法第74條規定，送達不能依同法第72條", ["行政程序法#74", "行政程序法#72"]),
        ("訴願人主張處分違法即行政程序法第111條", ["行政程序法#111"]),
        ("依土壤及地下水污染整治法第12條", ["土壤及地下水污染整治法#12"]),  # 連接詞在真實法規名內,不得切
        # 黏了贅字的名稱,對到本文中曾以乾淨左邊界出現過的法規名(最長後綴)
        ("依訴願法第14條規定。此觀訴願法第1條自明", ["訴願法#14", "訴願法#1"]),
        ("按建築法第91條。涉及辦理建築法第77條", ["建築法#91", "建築法#77"]),
        ("附表第2條行政罰法第42條", ["行政罰法#42"]),  # 前一個「條」不屬任何法規,當成邊界剝掉
        ("遂謂訴願法第1條自明", ["遂謂訴願法#1"]),  # 前綴不在清單、本文也沒乾淨出現過就不猜,原樣保留
        ("依空氣污染防制法（下稱空污法）第24條，空污法第63條", ["空氣污染防制法#24", "空氣污染防制法#63"]),  # 下稱別名展開
        ("依廢棄物清理法第27條,本法第50條", ["廢棄物清理法#27", "廢棄物清理法#50"]),  # 「本法」回溯
        ("依廢棄物清理法第27條，且未涉及本法第50條", ["廢棄物清理法#27", "廢棄物清理法#50"]),  # 回溯詞前黏了子句也算回溯
        ("113年1月1日施行之建築物使用類組及變更使用辦法第2條", ["建築物使用類組及變更使用辦法#2"]),  # 「之」切割
        ("按廢棄物清理法罰鍰額度裁罰準則第2條。裁罰準則第2條", ["廢棄物清理法罰鍰額度裁罰準則#2"]),  # 簡稱對到唯一乾淨全名
        # 全名餘部「建築物公共安全檢查」不含法規詞但有 9 字:贅字最長只有 5 字,長餘部視為名稱的一部分
        ("再按建築物公共安全檢查簽證及申報辦法第5條。簽證及申報辦法第5條", ["建築物公共安全檢查簽證及申報辦法#5"]),
        # 無引入的縮寫:字序是唯一一個同尾綴全名的子序列
        ("依空氣污染防制法第24條，符合空污法第96條", ["空氣污染防制法#24", "空氣污染防制法#96"]),
        ("「建築物公共安全檢查簽證及申報辦法」第5條規定甚明", ["建築物公共安全檢查簽證及申報辦法#5"]),  # 閉引號夾在名稱與「第」之間
        # 「並」剝掉後剩「無洗錢防制法」:前綴詞引出不等於整段是名稱,餘部 1 字仍是贅字
        ("依洗錢防制法第21條，並無洗錢防制法第22條之適用", ["洗錢防制法#21", "洗錢防制法#22"]),
        # 後文的簡稱以全名結尾、全名餘部含「法」:簡稱展開成全名,不得反過來把全名剔掉
        ("依空氣污染防制法應處罰鍰額度裁罰準則第3條規定，裁罰準則第3條附表", ["空氣污染防制法應處罰鍰額度裁罰準則#3"]),
        # 簡稱先出現、全名只在標點後出現,展開結果與順序無關
        ("按裁罰準則第2條。廢棄物清理法罰鍰額度裁罰準則第2條", ["廢棄物清理法罰鍰額度裁罰準則#2"]),
        # 「」內的標題是強詞典:名稱含「違反」也不得切;簡稱是唯一標題的後綴就展開成全名
        (
            "「公私場所固定污染源違反空氣污染防制法應處罰鍰額度裁罰準則」附表。"
            "按公私場所固定污染源違反空氣污染防制法應處罰鍰額度裁罰準則第2條，裁罰準則第3條",
            ["公私場所固定污染源違反空氣污染防制法應處罰鍰額度裁罰準則#2", "公私場所固定污染源違反空氣污染防制法應處罰鍰額度裁罰準則#3"],
        ),
        # 兩個標題都以「裁罰準則」結尾:簡稱不知道指哪一個,保留簡稱不猜
        ("「廢棄物清理法罰鍰額度裁罰準則」「公私場所固定污染源違反空氣污染防制法應處罰鍰額度裁罰準則」。裁罰準則第3條", ["裁罰準則#3"]),
        ("依裁罰準則第2條，前揭裁罰準則第3條", ["裁罰準則#2", "裁罰準則#3"]),  # 指代詞前綴
    ],
)
def test_extract_law_keys(text, expected):
    assert extract_law_keys(text) == expected


def test_extract_law_keys_discards_bare_suffix_after_stripping():
    """剝完前綴後長度 <2(如單獨一個「法」字)不得當成法規名。"""
    assert extract_law_keys("按法第3條規定") == []


# ---------- evaluate_case ----------


def _info(case_type="廢棄物清理法事件"):
    return CaseInfo(
        appellant="王大明",
        agency="彰化縣環境保護局",
        disposition_date="110年3月5日",
        disposition_no="彰環廢字第1號",
        disposition_summary="裁處罰鍰",
        case_type=case_type,
    )


class _AdmissibleProvider(AIProvider):
    """通過審查 -> F2 -> F3(相容案由)-> F4(駁回,理由夾帶一條清單外法條)。"""

    def extract_case_info(self, text):
        return _info()

    def screen_admissibility(self, info, text):
        return ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")

    def assess_standing(self, info, text):
        raise AssertionError("測資無 disposition_recipient,不應觸發")

    def recommend_laws(self, info):
        return [
            LawRef(law_name="廢棄物清理法", article_no="27", text="條文", amend_date="民國106年01月18日", relevance="相關"),
            LawRef(law_name="廢棄物清理法", article_no="50", text="條文", amend_date="民國106年01月18日", relevance="相關"),
        ]

    def get_law_articles(self, keys):
        raise AssertionError("admissible track 不應呼叫")

    def find_similar_cases(self, info, screening, text):
        return [
            SimilarCase(
                case_no="彰府訴字第1號", year="109", case_type="違反廢棄物清理法事件",
                appeal_article="無", issue="任意棄置", result="駁回", summary="摘要", similarity_note="相似",
            )
        ]

    def generate_draft(self, info, screening, laws, cases):
        return DraftResult(
            draft_type="駁回", fact="事實", reason="依廢棄物清理法第27條及行政罰法第7條裁處",
            main_text="訴願駁回。", cited_laws=["廢棄物清理法#27"],
        )


class _InadmissibleProvider(AIProvider):
    """不通過審查(逾期)-> 跳過 F2 -> F3 空清單 -> F4(不受理,體例由 pipeline 強制)。"""

    def extract_case_info(self, text):
        return _info(case_type="社會救助")

    def screen_admissibility(self, info, text):
        return ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期提起訴願")

    def assess_standing(self, info, text):
        raise AssertionError("測資無 disposition_recipient,不應觸發")

    def recommend_laws(self, info):
        raise AssertionError("inadmissible track 不應呼叫")

    def get_law_articles(self, keys):
        return [
            LawRef(law_name=k.split("#")[0], article_no=k.split("#")[1], text="條文", amend_date="未收錄", relevance="條號精查")
            for k in keys
        ]

    def find_similar_cases(self, info, screening, text):
        return []

    def generate_draft(self, info, screening, laws, cases):
        return DraftResult(
            draft_type="駁回", fact="模型自行補寫", reason="依訴願法第77條及行政罰法第18條應不受理",
            main_text="訴願駁回。", cited_laws=["訴願法#77"],
        )


def _run(case_id: str, provider: AIProvider) -> Case:
    store = MemoryStore()
    store.create(Case(case_id=case_id, created_at="2026-08-17T00:00:00", title="測試案", source="text", input_text="訴願書原文"))
    run_case(case_id, store, provider)
    return store.get(case_id)


def test_evaluate_case_admissible_track_all_keys():
    case = _run("c-eval-admis", _AdmissibleProvider())
    label = {"decision": "駁回", "clause": None, "case_type": "廢棄物清理法事件", "laws": ["廢棄物清理法#27"]}

    row = evaluate_case(case, label)

    assert row["case_dir"] == "c-eval-admis"
    assert row["status"] == "done"
    assert row["track"] == "admissible"
    assert row["decision_pred"] == "駁回"
    assert row["decision_true"] == "駁回"
    assert row["decision_hit"] is True
    assert row["clause_pred"] is None
    assert row["clause_true"] is None
    assert row["clause_hit"] is None  # clause_true 為 None 不計
    assert row["law_recall"] == 1.0
    assert row["law_truth_n"] == 1
    assert row["case_hit"] is True  # 案由「違反廢棄物清理法事件」與 label 相容,結果同為駁回
    assert row["cited_in_text"] == 2
    assert row["hallucinated"] == ["行政罰法#7"]  # 不在 f2 清單內
    assert isinstance(row["needs_review"], bool)
    assert row["seconds"] is None  # evaluate_case 本身不計時,由 run() 填入


def test_evaluate_case_inadmissible_track_all_keys():
    case = _run("c-eval-inadm", _InadmissibleProvider())
    label = {
        "decision": "不受理", "clause": "77(2)", "case_type": "社會救助",
        "laws": ["訴願法#77", "訴願法#14", "行政程序法#72"],
    }

    row = evaluate_case(case, label)

    assert row["track"] == "inadmissible"
    assert row["decision_pred"] == "不受理"  # enforce_inadmissible_format 強制體例
    assert row["decision_hit"] is True
    assert row["clause_pred"] == "77(2)"
    assert row["clause_hit"] is True
    assert row["law_recall"] == 1.0
    assert row["case_hit"] is False  # f3 為空清單
    assert row["hallucinated"] == ["行政罰法#18"]  # 不受理可引清單無此法條
    assert row["cited_in_text"] == 2


def test_evaluate_case_clause_true_present_but_screening_passed_counts_as_miss():
    """screening.passed=True(clause_pred 恆為 None)但 label 期待有款次 -> 算 miss,不是不計。"""
    case = _run("c-eval-missmatch", _AdmissibleProvider())
    label = {"decision": "駁回", "clause": "77(2)", "case_type": "廢棄物清理法事件", "laws": []}

    row = evaluate_case(case, label)

    assert row["clause_pred"] is None
    assert row["clause_hit"] is False  # 不是 None


def test_evaluate_case_error_status_all_hits_none():
    """失敗路徑:status!=done 的案子,所有 hit/recall 為 None,不得誤判為命中或不中。"""
    case = Case(
        case_id="c-eval-error", created_at="2026-08-17T00:00:00", title="失敗案",
        status="error", source="pdf", input_text="", error="模擬失敗",
    )
    label = {"decision": "駁回", "clause": "77(2)", "case_type": "廢棄物清理", "laws": ["訴願法#77"]}

    row = evaluate_case(case, label)

    assert row["status"] == "error"
    assert row["decision_pred"] is None
    assert row["decision_hit"] is None
    assert row["clause_hit"] is None
    assert row["law_recall"] is None
    assert row["case_hit"] is None
    assert row["cited_in_text"] == 0
    assert row["hallucinated"] == []
    assert isinstance(row["needs_review"], bool)


def test_evaluate_case_law_recall_empty_truth_is_none():
    case = _run("c-eval-notruth", _AdmissibleProvider())
    label = {"decision": "駁回", "clause": None, "case_type": "廢棄物清理法事件", "laws": []}

    row = evaluate_case(case, label)

    assert row["law_recall"] is None
    assert row["law_truth_n"] == 0


# ---------- aggregate ----------


def test_aggregate_excludes_none_from_denominators():
    """核心行為:None 的單案不計入分母,兩種異質輸入(True/False vs 全 None)不可用單一硬編碼值通過。"""
    rows = [
        {
            "case_dir": "a", "status": "done", "track": "admissible",
            "decision_pred": "駁回", "decision_true": "駁回", "decision_hit": True,
            "clause_pred": None, "clause_true": None, "clause_hit": None,
            "law_recall": 1.0, "law_truth_n": 1, "case_hit": True,
            "cited_in_text": 2, "hallucinated": ["行政罰法#7"], "needs_review": False, "seconds": 0.1,
        },
        {
            "case_dir": "b", "status": "done", "track": "inadmissible",
            "decision_pred": "不受理", "decision_true": "不受理", "decision_hit": True,
            "clause_pred": "77(2)", "clause_true": "77(2)", "clause_hit": True,
            "law_recall": 1.0, "law_truth_n": 3, "case_hit": False,
            "cited_in_text": 2, "hallucinated": ["行政罰法#18"], "needs_review": True, "seconds": 0.2,
        },
        {
            "case_dir": "c", "status": "error", "track": None,
            "decision_pred": None, "decision_true": "駁回", "decision_hit": None,
            "clause_pred": None, "clause_true": "77(2)", "clause_hit": None,
            "law_recall": None, "law_truth_n": 1, "case_hit": None,
            "cited_in_text": 0, "hallucinated": [], "needs_review": False, "seconds": None,
        },
    ]

    agg = aggregate(rows)

    assert agg["n"] == 3
    assert agg["error_n"] == 1
    assert agg["decision_accuracy"] == 1.0  # 2 個非 None,皆 True
    assert agg["clause_accuracy"] == 1.0  # 只有 row b 的 clause_hit 非 None
    assert agg["law_recall_at_10"] == 1.0  # row c 的 None 不計入
    assert agg["case_top3_hit_rate"] == 0.5  # True, False -> 0.5(row c 的 None 不計)
    assert agg["citation_hallucination_rate"] == 2 / 4  # sum(hallucinated)=2, sum(cited_in_text)=4
    assert agg["needs_review_rate"] == pytest.approx(1 / 3)
    assert set(agg.keys()) == {
        "n", "decision_accuracy", "clause_accuracy", "law_recall_at_10",
        "case_top3_hit_rate", "citation_hallucination_rate", "needs_review_rate", "error_n",
    }


def test_aggregate_zero_citations_gives_none_hallucination_rate():
    rows = [
        {
            "case_dir": "a", "status": "done", "track": "admissible",
            "decision_pred": "駁回", "decision_true": "駁回", "decision_hit": True,
            "clause_pred": None, "clause_true": None, "clause_hit": None,
            "law_recall": None, "law_truth_n": 0, "case_hit": None,
            "cited_in_text": 0, "hallucinated": [], "needs_review": False, "seconds": 0.1,
        }
    ]
    assert aggregate(rows)["citation_hallucination_rate"] is None


# ---------- run() ----------


def _write_pdf(path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), text, fontsize=14)
    doc.save(str(path))
    doc.close()


class _CliStubProvider(AIProvider):
    def extract_case_info(self, text):
        return _info()

    def screen_admissibility(self, info, text):
        return ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")

    def assess_standing(self, info, text):
        raise AssertionError("不應觸發")

    def recommend_laws(self, info):
        return [LawRef(law_name="廢棄物清理法", article_no="27", text="條文", amend_date="未收錄", relevance="相關")]

    def get_law_articles(self, keys):
        raise AssertionError("不應觸發")

    def find_similar_cases(self, info, screening, text):
        return []

    def generate_draft(self, info, screening, laws, cases):
        return DraftResult(draft_type="駁回", fact="事實", reason="理由", main_text="訴願駁回。", cited_laws=[])


def test_run_skips_folders_without_any_slot_file(tmp_path, capsys):
    """參考資料夾(只有 .md)不是案例:不查 labels、不 raise,但要印出跳過。"""
    (tmp_path / "_參考").mkdir()
    (tmp_path / "_參考" / "note.md").write_text("x", encoding="utf-8")

    result = run(tmp_path, {}, provider=None)

    assert result["rows"] == []
    assert "[skip] _參考" in capsys.readouterr().out


def test_run_raises_when_label_missing_for_case_folder(tmp_path):
    case_dir = tmp_path / "cases" / "case1"
    case_dir.mkdir(parents=True)
    _write_pdf(case_dir / "01_訴願書.pdf", "訴願書內容")
    _write_pdf(case_dir / "02_原處分書.pdf", "原處分書內容")

    with pytest.raises(KeyError):
        run(tmp_path / "cases", {}, _CliStubProvider(), None)


def test_run_missing_required_slot_is_error_not_raise(tmp_path):
    case_dir = tmp_path / "cases" / "case2"
    case_dir.mkdir(parents=True)
    _write_pdf(case_dir / "01_訴願書.pdf", "訴願書內容")  # 缺 02_原處分書.pdf

    labels = {"case2": {"decision": "駁回", "clause": None, "case_type": "x", "laws": []}}

    report = run(tmp_path / "cases", labels, _CliStubProvider(), None)

    assert report["rows"][0]["case_dir"] == "case2"
    assert report["rows"][0]["status"] == "error"
    assert report["rows"][0]["decision_hit"] is None
    assert report["aggregate"]["error_n"] == 1


def test_run_completes_case_with_optional_slots_missing_and_writes_out(tmp_path):
    case_dir = tmp_path / "cases" / "case3"
    case_dir.mkdir(parents=True)
    _write_pdf(case_dir / "01_訴願書.pdf", "訴願書內容")
    _write_pdf(case_dir / "02_原處分書.pdf", "原處分書內容")
    # 03/04 不建立 -> 不得報錯

    labels = {"case3": {"decision": "駁回", "clause": None, "case_type": "廢棄物清理法事件", "laws": ["廢棄物清理法#27"]}}
    out_path = tmp_path / "report.json"

    report = run(tmp_path / "cases", labels, _CliStubProvider(), out_path)

    row = report["rows"][0]
    assert row["status"] == "done"
    assert row["decision_pred"] == "駁回"
    assert row["seconds"] is not None and row["seconds"] >= 0

    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["rows"][0]["case_dir"] == "case3"
    assert saved["aggregate"]["n"] == 1
