"""API / store / 前端共用的 Pydantic models。"""
from __future__ import annotations

import re
from datetime import date
from typing import Literal, Optional, get_args

from pydantic import BaseModel


class CaseInfo(BaseModel):
    appellant: str
    agency: str
    disposition_date: str
    disposition_no: str
    disposition_summary: str
    appeal_reasons: list[str] = []
    case_type: str
    issues: list[str] = []
    cited_articles: list[str] = []  # 如 "廢棄物清理法#46"
    # 以下六欄取自送達證書/原處分書/訴願書,期間計算(deadline_extract)與程序審查才用得到;
    # 全部預設空字串,舊樣本/舊測資不補這幾欄仍可通過驗證(見階段一文書規格與期間計算.md §一/§二/§三)
    receipt_date: str = ""  # 訴願書「收受或知悉行政處分日期」(訴願法§56 I⑥),非送達日期
    service_date: str = ""  # 送達證書「送達時間」欄,即送達生效日(原文寫法,不換算)
    service_method: str = ""  # 送達方式:本人/同居人/受雇人/接收郵件人員/留置/寄存(§72-74)
    disposition_fine: str = ""  # 原處分書罰鍰金額(原文寫法)
    disposition_notice_clause: str = ""  # 原處分書教示條款原文,有無教示影響救濟期間認定
    disposition_recipient: str = ""  # 原處分相對人;多數與 appellant 同一人,但代理/繼受案可能不同


class ScreeningResult(BaseModel):
    passed: bool
    matched_clause: Optional[str] = None  # 如 "77條第2款"
    reasoning: str
    review_note: str = ""  # 需人工複核的原因,空字串代表結論可逕採(語意同 DeadlineCheck.review_note)


# 款次由 LLM 產出,條文本身以「一、二、…」列款,故中文數字與阿拉伯數字都要收
_CLAUSE_RE = re.compile(r"(\d+)\s*條\s*第\s*([0-9]+|[一二三四五六七八九十]+)\s*款")
_CHINESE_DIGITS = {c: i for i, c in enumerate("一二三四五六七八九", start=1)}


def _clause_int(text: str) -> Optional[int]:
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if len(text) == 1:
        return _CHINESE_DIGITS.get(text)
    if len(text) == 2 and text[0] == "十":  # 十一~十九
        return 10 + _CHINESE_DIGITS.get(text[1], 0)
    return None


def parse_clause(matched_clause: Optional[str]) -> Optional[tuple[int, int]]:
    """「77條第2款」/「77條第二款」-> (77, 2);解析不出回 None,由呼叫端決定退路。"""
    m = _CLAUSE_RE.search(matched_clause or "")
    if not m:
        return None
    clause = _clause_int(m.group(2))
    return (int(m.group(1)), clause) if clause is not None else None


class StandingAssessment(BaseModel):
    """provider.assess_standing 的結構化回傳(§77(3)保護規範判準,Ticket 6):不是自由論述,
    而是「所憑保護規範」與「是否及於訴願人」兩個獨立欄位。referenced_norm 留空(模型指不出
    具體法規名稱＋條號)時,has_standing 不論填了什麼都不得採用——見
    procedural_checks.resolve_standing_assessment,這裡只誠實記錄「模型說了什麼」。"""

    referenced_norm: str = ""  # 所憑保護規範,格式「法規名稱#條號」,如「廢棄物清理法#27」
    has_standing: Optional[bool] = None


class DeadlineCheck(BaseModel):
    """訴願期間計算結果。overdue 為 None 代表無從認定,不等於未逾期。"""

    overdue: Optional[bool] = None
    service_date: Optional[date] = None
    due_date: Optional[date] = None
    filed_date: Optional[date] = None
    detail: str = ""  # 算式敘述,不受理決定書理由可直接引用
    review_note: str = ""  # 需人工複核的原因,空字串代表結論可逕採


class LawRef(BaseModel):
    law_name: str
    article_no: str
    text: str
    amend_date: str
    source_key: Optional[str] = None
    relevance: str


class SimilarCase(BaseModel):
    case_no: str
    year: str
    case_type: str
    appeal_article: str
    issue: str
    result: str
    summary: str
    similarity_note: str
    source_key: Optional[str] = None


class DraftResult(BaseModel):
    draft_type: DraftType
    fact: str
    reason: str
    main_text: str
    cited_laws: list[str] = []


class DraftVersion(BaseModel):
    """草稿的一個歷史版本。每次 PATCH 存一版,定稿後的修訂也一樣存——定稿只是標記,不鎖。"""

    saved_at: str
    fact: str
    reason: str
    main_text: str


class DraftPatch(BaseModel):
    """PATCH /api/cases/{id}/draft 的 body。"""

    fact: str
    reason: str
    main_text: str
    # 送出時前端帶目前的版本數;與伺服器端不符即回 409。三種 store 對 Case 都是整包
    # read-modify-write,兩個視窗同時 PATCH 的結果是後送出的那份無聲蓋掉前一份,而兩邊都
    # 以為自己存成功了(見實作計畫 Ticket 10)。None 代表呼叫端未帶版本(舊前端),不檢查。
    base_version: Optional[int] = None


class ScreeningOverride(BaseModel):
    """PATCH /api/cases/{id}/screening 的 body:承辦人推翻程序審查結論。
    自動判之後承辦人只做確認,那就必須有推翻的入口——否則自動判等於終局判斷。"""

    passed: bool
    matched_clause: Optional[str] = None
    reasoning: str


Stage = Literal["f1", "screening", "f2", "f3", "f4", "done"]
Status = Literal["collecting", "processing", "done", "error"]
Track = Literal["admissible", "inadmissible"]
Source = Literal["pdf", "text"]
DocumentSlot = Literal["appeal", "service", "disposition", "answer"]
DraftType = Literal["不受理", "駁回", "撤銷另處", "原處分撤銷", "部分不受理部分駁回"]
DRAFT_TYPES: tuple[str, ...] = get_args(DraftType)  # providers 的 JSON schema enum 與這裡同源

# 各槽對應的中文名,錯誤訊息與前端顯示共用同一份,不分別寫兩次
DOCUMENT_SLOT_LABELS: dict[DocumentSlot, str] = {
    "appeal": "訴願書",
    "service": "送達證書",
    "disposition": "原處分書",
    "answer": "訴願答辯書",
}


class DocumentCheck(BaseModel):
    """文件型態確認結果。matched=None 代表規則判斷不出來、Gemini 亦未能確認(或未設定金鑰),須人工核對——
    不可靜默當作「已確認正確」,這正是規則式判斷失效時最容易被忽略的一步。"""

    matched: Optional[bool] = None
    method: Literal["rule", "gemini", "none"] = "none"  # 這次結論由哪一層判斷出來
    note: str = ""  # 判斷依據或「無法確認」原因,供人工核對


# 經 OCR 取得文字的槽一律標這句:模型抽字會編字,而本系統的正確性建立在日期上。
# 凡是這一槽的日期,都不得據以覆寫程序審查(見實作計畫 Ticket 4)。
OCR_REVIEW_NOTE = "本槽文字由 OCR 取得,日期須人工核對原件"


class CaseDocument(BaseModel):
    """一個文件槽(訴願書/送達證書/原處分書)的輸入內容與型態確認結果。"""

    slot: DocumentSlot
    source: Source
    text: str
    check: DocumentCheck = DocumentCheck()
    ocr: bool = False  # 文字由掃描件逐頁抽字取得,非 PDF 文字層
    review_note: str = ""  # 這一槽本身需人工複核的原因(如 OCR 取字),空字串代表無


class DocumentReplace(BaseModel):
    """PATCH /api/cases/{id}/documents/{slot} 的 body(文字輸入時使用;檔案走 multipart 另一路徑)。"""

    text: str


def build_input_text(documents: dict[DocumentSlot, CaseDocument]) -> str:
    """三槽合一成 F1/程序審查吃的合併字串,分段標頭讓 F1 擷取知道欄位該從哪一段找。
    單一真相是 documents[slot].text,input_text 只是它的衍生值——收案、重傳、store 讀回
    都必須呼叫這支函式重建,不能各自維護一份,否則重傳文件後分析用的仍是舊文字而畫面上
    完全看不出來(見實作計畫 Ticket 3a/3b)。缺槽時該段留空,不省略標頭。"""
    return "\n\n".join(
        f"【{DOCUMENT_SLOT_LABELS[slot]}】\n{documents[slot].text if slot in documents else ''}"
        for slot in ("appeal", "service", "disposition", "answer")
    )


MAX_DRAFT_VERSIONS = 20  # DynamoDB 單筆 400KB 上限;版本歷史會單向成長,不設上限就是等它某天崩掉


class Case(BaseModel):
    """store 與前端共用的完整案件物件 schema。"""

    case_id: str
    created_at: str
    title: str
    status: Status = "collecting"
    current_stage: Stage = "f1"
    track: Optional[Track] = None
    source: Source
    input_text: str
    documents: dict[DocumentSlot, CaseDocument] = {}
    f1: Optional[CaseInfo] = None
    screening: Optional[ScreeningResult] = None
    # 第一次被人工推翻時把系統原判搬進來,screening 留現行(人工)結論。事後看得出「系統判什麼、
    # 人改成什麼」,而且「有沒有被推翻過」變成可判斷的事實(非 None 即是),不必另立旗標。
    screening_system: Optional[ScreeningResult] = None
    deadline: Optional[DeadlineCheck] = None
    f2: Optional[list[LawRef]] = None
    f3: Optional[list[SimilarCase]] = None
    f4: Optional[DraftResult] = None
    draft_versions: list[DraftVersion] = []
    draft_versions_truncated: bool = False  # 有版本被丟掉這件事要看得見,不是靜默消失
    finalized_at: Optional[str] = None  # 定稿只是標記,不鎖;定稿後仍可 PATCH,改了再存一版
    error: Optional[str] = None


class CaseSummary(BaseModel):
    case_id: str
    created_at: str
    title: str
    status: Status
    track: Optional[Track] = None
    current_stage: Stage
    case_type: Optional[str] = None
    # 清單上要一眼看出哪幾件不能直接送:待人工確認的案子與正常案子長得一模一樣是最糟的
    needs_review: bool = False
