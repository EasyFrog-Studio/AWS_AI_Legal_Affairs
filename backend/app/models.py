"""API / store / 前端共用的 Pydantic models。"""
from __future__ import annotations

import re
from datetime import date
from typing import Literal, Optional, get_args

from pydantic import BaseModel, computed_field, model_validator

from app.law_urls import interpretation_url, law_article_url


# 送達方式的法定值域(訴願法準用行政程序法§72-74)。送達證書是勾選式表單,六個選項的文字
# 全在同一頁的文字層裡,不把值域交給模型就會整段抄寫;寄存地點對期間無影響(生效日即寄存當日),
# 故只留「寄存」不留地點。
SERVICE_METHODS = ("本人", "同居人", "受雇人", "接收郵件人員", "留置", "寄存", "未載明")


class CaseInfo(BaseModel):
    appellant: str
    agency: str
    disposition_date: str
    disposition_no: str
    disposition_summary: str
    appeal_facts: list[str] = []  # 訴願人自述的事實經過(§56 I⑤「訴願之事實及理由」的前半)
    appeal_reasons: list[str] = []
    case_type: str
    issues: list[str] = []
    cited_articles: list[str] = []  # 如 "廢棄物清理法#46"
    # 以下六欄取自送達證書/原處分書/訴願書,期間計算(deadline_extract)與程序審查才用得到;
    # 全部預設空字串,舊樣本/舊測資不補這幾欄仍可通過驗證
    receipt_date: str = ""  # 訴願書「收受或知悉行政處分日期」(訴願法§56 I⑥),非送達日期
    service_date: str = ""  # 送達證書「送達時間」欄,即送達生效日(原文寫法,不換算)
    service_method: str = ""  # 送達方式,值域見 SERVICE_METHODS(§72-74)
    disposition_fine: str = ""  # 原處分書罰鍰金額(原文寫法)
    disposition_notice_clause: str = ""  # 原處分書教示條款原文,有無教示影響救濟期間認定
    disposition_recipient: str = ""  # 原處分相對人;多數與 appellant 同一人,但代理/繼受案可能不同
    # 以下三欄取自訴願答辯書(第四槽,選填)。機關受理後才送來,收案當下本來就沒有,
    # 故皆預設空值;答辯書的內容只能填這三欄,不得用來填訴願人那一側的欄位
    answer_statement: str = ""  # 答辯聲明原文
    answer_self_revoked: str = ""  # 機關是否已自行撤銷或變更原處分(原文寫法)
    answer_arguments: list[str] = []  # 機關的答辯主張,逐條列出


class ScreeningResult(BaseModel):
    passed: bool
    matched_clause: Optional[str] = None  # 如 "77條第2款"
    reasoning: str
    review_note: str = ""  # 需人工複核的原因,空字串代表結論可逕採(語意同 DeadlineCheck.review_note)


def join_review_notes(*notes: Optional[str]) -> str:
    """保留事項一律附加:多個檢核同時成立時整段覆寫,承辦人就只看得到最後一個複核理由。"""
    return ";".join(n for n in notes if n)


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
    """provider.assess_standing 的結構化回傳(§77(3)保護規範判準):不是自由論述,
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
    # 註記非空不必然表示算式本身不可信:有些歧異法律上已有定論(生效日以送達證書為準),
    # 要讓承辦人看到,但不該讓算式閉嘴。reconcile_deadline 只看這個旗標決定要不要覆寫
    override_blocked: bool = False


class LawRef(BaseModel):
    law_name: str
    article_no: str
    text: str
    amend_date: str
    source_key: Optional[str] = None
    relevance: str

    @computed_field
    @property
    def source_url(self) -> Optional[str]:
        """全國法規資料庫的條文連結。算出來而不是逐點填:F2 檢索、不受理法源精查、
        DynamoDB 補全三處都生 LawRef,任一處忘了填就是少一個連結而不會有人發現。"""
        return law_article_url(self.law_name, self.article_no)


class ReferenceRef(BaseModel):
    """F2+ 參考見解。與 LawRef 平行而非共用:函釋/釋字/裁判填不出 law_name#article_no,
    塞進 LawRef 會讓那個鍵帶著空值流進 F4 可引用清單與 law_articles 精查。"""

    doc_kind: str
    name: str
    issuer: str = ""
    issued_date: str
    topic: str = ""
    text: str
    source_key: Optional[str] = None
    relevance: str

    @computed_field
    @property
    def source_url(self) -> Optional[str]:
        """釋字推得出,函釋與裁判推不出來(見 law_urls.interpretation_url)。"""
        return interpretation_url(self.doc_kind, self.name)


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
    source_url: Optional[str] = None  # 爬蟲語料帶的原始查詢系統深連結,官方語料沒有


class DraftResult(BaseModel):
    draft_type: DraftType
    fact: str
    reason: str
    main_text: str
    cited_laws: list[str] = []


class DecisionHeader(BaseModel):
    """決定書上系統填不出來的欄位,一律留白。決定書產出時攤平成全文(decision_plain_text),
    承辦人直接在那份全文裡填——欄位本身沒有寫入端點,改表頭就是改那份文字。"""

    case_no: str = ""
    appellant: str = ""
    agency: str = ""
    chairman: str = ""
    committee: str = ""  # 一行一位委員;留空則維持 12 行空白
    decided_date: str = ""


# 決定書本文三段的標題與 f4 欄位,版面(pdf_render)與舊版本快照攤平共用同一張表
DRAFT_SECTIONS = (("主　文", "main_text"), ("事　實", "fact"), ("理　由", "reason"))


class DraftVersion(BaseModel):
    """草稿的一個歷史版本。每次 PATCH 存一版,定稿後的修訂也一樣存——定稿只是標記,不鎖。
    存的是整份決定書全文:編輯單位就是這一整份,拆成三欄存會對不上承辦人實際改的東西。"""

    saved_at: str
    text: str

    @model_validator(mode="before")
    @classmethod
    def _flatten_legacy_fields(cls, data):
        # store 內仍有主文/事實/理由三欄格式的快照,讀出時攤成全文,空欄不印標題
        if isinstance(data, dict) and "text" not in data and "main_text" in data:
            text = "\n\n".join(f"{heading}\n{data[key]}" for heading, key in DRAFT_SECTIONS if data.get(key))
            return {"saved_at": data.get("saved_at"), "text": text}
        return data


class DraftTextPatch(BaseModel):
    """決定書全文的一次修改。base_version 用來擋兩個視窗互相無聲覆寫,語意同原本的 DraftPatch。"""

    text: str
    base_version: Optional[int] = None


class ScreeningOverride(BaseModel):
    """PATCH /api/cases/{id}/screening 的 body:承辦人推翻程序審查結論。
    自動判之後承辦人只做確認,那就必須有推翻的入口——否則自動判等於終局判斷。"""

    passed: bool
    matched_clause: Optional[str] = None
    reasoning: str


class DraftResultOverride(BaseModel):
    """PATCH /api/cases/{id}/draft/result 的 body:承辦人改決定結果,不動全文與其餘 f4 欄位。"""

    draft_type: DraftType


Stage = Literal["f1", "screening", "f2", "f2_refs", "f3", "f4", "done"]
Status = Literal["collecting", "processing", "done", "error"]
Track = Literal["admissible", "inadmissible"]
Source = Literal["pdf", "text"]
DocumentSlot = Literal["appeal", "service", "disposition", "answer"]
DraftType = Literal["不受理", "駁回", "撤銷另處", "原處分撤銷", "部分不受理部分駁回"]
DRAFT_TYPES: tuple[str, ...] = get_args(DraftType)  # providers 的 JSON schema enum 與這裡同源


def draft_types_for(passed: bool) -> tuple[str, ...]:
    """依程序審查結論收斂 F4 的 draft_type 值域,供 provider 組 JSON schema。

    受理案不得產出「不受理」:那是分流的職權,不是草稿的。prompt 講過,但 schema 五值全開時
    模型照樣挑得到,結果是 track=admissible 而草稿寫不受理。不受理側不在此收——
    enforce_inadmissible_format 已在事後校正體例。
    """
    if not passed:
        return DRAFT_TYPES
    return tuple(t for t in DRAFT_TYPES if t != "不受理")

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
# 凡是這一槽的日期,都不得據以覆寫程序審查。
OCR_REVIEW_NOTE = "本槽文字由 OCR 取得，日期須人工核對原件"


class CaseDocument(BaseModel):
    """一個文件槽(訴願書/送達證書/原處分書)的輸入內容與型態確認結果。"""

    slot: DocumentSlot
    source: Source
    text: str
    check: DocumentCheck = DocumentCheck()
    ocr: bool = False  # 文字由掃描件逐頁抽字取得,非 PDF 文字層
    filename: str = ""  # 上傳的原始檔名,貼上文字為空;舊案沒有這欄,讀回時視為空
    review_note: str = ""  # 這一槽本身需人工複核的原因(如 OCR 取字),空字串代表無


class DocumentReplace(BaseModel):
    """PATCH /api/cases/{id}/documents/{slot} 的 body(文字輸入時使用;檔案走 multipart 另一路徑)。"""

    text: str


def build_input_text(documents: dict[DocumentSlot, CaseDocument]) -> str:
    """必填三槽(訴願書/送達證書/原處分書)加選填答辯書共四槽合一成 F1/程序審查吃的合併字串,
    分段標頭讓 F1 擷取知道欄位該從哪一段找。
    單一真相是 documents[slot].text,input_text 只是它的衍生值——收案、重傳、store 讀回
    都必須呼叫這支函式重建,不能各自維護一份,否則重傳文件後分析用的仍是舊文字而畫面上
    完全看不出來。缺槽時該段留空,不省略標頭。"""
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
    # 承辦人改過 f1 之後重跑,不得再呼叫 extract_case_info——人剛改的欄位會被模型改回去,
    # 與 screening_system 擋的是同一種失效
    f1_edited: bool = False
    # 第一次被人工修改時把模型原本抽的整份搬進來,f1 留現行(人工)值。前端據此逐欄標出
    # 「這一欄被改過」——改完若與模型抽的長得一樣,承辦人下次就分不出哪些字是自己確認過的
    f1_system: Optional["CaseInfo"] = None
    screening: Optional[ScreeningResult] = None
    # 第一次被人工推翻時把系統原判搬進來,screening 留現行(人工)結論。事後看得出「系統判什麼、
    # 人改成什麼」,而且「有沒有被推翻過」變成可判斷的事實(非 None 即是),不必另立旗標。
    screening_system: Optional[ScreeningResult] = None
    deadline: Optional[DeadlineCheck] = None
    f2: Optional[list[LawRef]] = None
    # F2+ 參考見解。與 f2 不同,兩條 track 都會有:不受理決定書的理由欄一樣要論證
    f2_refs: Optional[list[ReferenceRef]] = None
    f3: Optional[list[SimilarCase]] = None
    f4: Optional[DraftResult] = None
    # 第一次被人工修改決定結果時把系統原判整份搬進來,f4 留現行(人工)值,比照 screening_system 的做法
    f4_system: Optional[DraftResult] = None
    decision_header: DecisionHeader = DecisionHeader()
    # 決定書全文。承辦人實際編輯與下載的就是這一份;f4 三欄是模型產出的原始素材,產出時攤平成這份文字
    draft_plain_text: str = ""
    draft_versions: list[DraftVersion] = []
    draft_versions_truncated: bool = False  # 有版本被丟掉這件事要看得見,不是靜默消失
    finalized_at: Optional[str] = None  # 定稿只是標記,不鎖;定稿後仍可 PATCH,改了再存一版
    error: Optional[str] = None

    @model_validator(mode="after")
    def _fill_plain_text_from_f4(self):
        # store 內仍有只有 f4、沒有全文的案件;讀出時攤平,否則畫面與下載都是空白
        if self.f4 is not None and not self.draft_plain_text:
            from app.pdf_render import decision_plain_text  # pdf_render 載入 fitz,不在模型模組載入時付這個代價

            self.draft_plain_text = decision_plain_text(self)
        return self



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
    # 最終結果(f4.draft_type),清單的「狀況」欄;還沒產出草稿就是 None
    result: Optional[DraftType] = None
    # 任一送來的文件被判定不是該類文件:案件卡在收案,清單要標成處理失敗而不是待確認
    documents_failed: bool = False
