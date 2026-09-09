"""從卷內文字抽出期間計算所需事實;抽不到就回報原因,不以預設值頂替。"""
import re
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel

from app.deadline import APPEAL_PERIOD_DAYS, format_roc, public_notice_service_date, roc_to_date
from app.text_quality import is_unreadable

# 具名群組:各式寫法的日期位置不同,改用名稱取值,日後在樣式裡多加一組括號也不會錯位。
# 公開名稱:notice_clause 抽更正通知送達日時共用同一份日期文法,不各自複製一份免得日後走樣。
ROC_DATE_PATTERN = r"(?P<y>\d{2,3})\s*年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日"
_DEFAULT_PERIOD_DAYS = APPEAL_PERIOD_DAYS  # 訴願法§14 I;補正期間 20 日另由文字抽出

# 訴願書(新北市法制局範本)的欄位名;訴願法§56 I⑥ 的法定記載事項。
# 拉成獨立常數:分槽抽取(extract_from_documents)只信這一個 pattern 當 receipt_date,
# 不比對其餘泛用的「送達/收受」寫法——那些是給決定書理由(單一字串)用的,訴願書原文不需要那麼寬。
_ERA = r"(?:中華民國)?\s*"  # 表單格內的日期恆冠國號,欄名冒號與數字之間隔著它
_RECEIPT_DATE_PATTERN = rf"收受或知悉行政處分(?:之年、?月、?日|日期)[:：]?\s*{_ERA}{ROC_DATE_PATTERN}"

# 送達生效日的各種寫法:寄存送達寫「寄存」,自行送達寫「簽收日期為」,均指同一件事
# 後文一律用 lookahead 不吃進去,否則「A 日及 B 日送達」只會抽到 A,一案兩處分就看不出來
# 換行也排除:表單式文件一格一行,跨行比對會把上一格的發文日期當成送達日
_SERVICE_PATTERNS = [
    rf"{ROC_DATE_PATTERN}(?=[^，。、（）\n]{{0,20}}?(?:送達|寄存))",
    rf"簽收日期為\s*{ROC_DATE_PATTERN}",
    _RECEIPT_DATE_PATTERN,
    # 送達證書(法務部範本)的欄位名,格內書「中華民國○年○月○日○午○時○分」
    rf"送達時間(?:（[^）]*）)?[^0-9]{{0,8}}?{ROC_DATE_PATTERN}",
    rf"{ROC_DATE_PATTERN}(?=[^。]{{0,20}}?生(?:合法)?送達效力)",
    rf"{ROC_DATE_PATTERN}(?=[^。]{{0,30}}?(?:訴願人|本人)[^。]{{0,6}}?收受)",
    # 訴願書依訴願法§56 I⑥ 自書「於○年○月○日收受」;排除「收受訴願書」——那是機關收文,不是送達
    rf"{ROC_DATE_PATTERN}(?=[^。]{{0,12}}?收受(?!訴願))",
]
# 送達證書槽專用的子集:只保留送達證書會出現的寫法(欄位名、寄存/簽收字樣),
# 不含「收受或知悉行政處分」與泛用的「收受」lookahead——那兩個是訴願書自述用的措辭,
# 送達證書上不會這樣寫,混進來只會製造假陽性。
_SERVICE_CERT_PATTERNS = [
    rf"{ROC_DATE_PATTERN}(?=[^，。、（）\n]{{0,20}}?(?:送達|寄存))",
    rf"簽收日期為\s*{ROC_DATE_PATTERN}",
    rf"送達時間(?:（[^）]*）)?[^0-9]{{0,8}}?{ROC_DATE_PATTERN}",
]
# 收文日:訴願法§14 III 以機關收受訴願書之日為準,故訴願書上的收文戳優先於敘述性寫法——
# 訴願書內文常有「於X日始至郵局領取」這類敘述,與收文戳並列會讓兩個日期互相抵銷而整筆作廢。
_FILED_STAMP_PATTERNS = [rf"收文日期[:：]\s*{_ERA}{ROC_DATE_PATTERN}"]
# 備援:決定書寫「訴願人遲至X始提起」,卷內沒有收文戳時才用
_FILED_PATTERNS = [rf"(?:遲至|於)\s*{ROC_DATE_PATTERN}(?=[^。]{{0,15}}?始)"]
_TRANSIT_DAYS_PATTERNS = [
    r"扣除\s*在途期間\s*(\d+)\s*日",
    r"扣除\s*(\d+)\s*日\s*之?\s*在途期間",
]
# 「無」單獨一字太鬆,只收語料實際出現的兩種寫法
_TRANSIT_NONE = re.compile(r"(?:毋須|無須|不須)[^，。]{0,12}?在途期間(?!辦法)|無在途期間之適用")
_CORRECTION_PERIOD = re.compile(r"(\d+)\s*日\s*之?\s*補正期間")
_START = re.compile(rf"應\s*(?:分別)?自[^。]{{0,40}}?{ROC_DATE_PATTERN}[^。]{{0,12}}?起?算")
_STATEMENT_END = re.compile(r"[惟然]")
_STATEMENT_SPAN = 200  # 起算日之後到「惟/然訴願人遲至」為止即認定段,超出則視為離題
# 公示送達的生效日另有等待期(行政程序法§81),與一般送達分開抽
_PUBLIC_NOTICE = re.compile(r"公示送達")
# §78 I③ 與 §79 的等待期不同(60 日 / 翌日),但這兩個條號恆出現在決定書引述的條文裡,
# 拿它們選分支等於拿樣板文字當認定依據,故只用來判「說不準」,不用來選分支
_PUBLIC_NOTICE_VARIANT = re.compile(
    r"第\s*78\s*條第\s*1\s*項第\s*3\s*款|第七十八條第一項第三款|於外國或境外"
    r"|第\s*79\s*條|第七十九條|同一當事人仍應為公示送達"
)
_POSTED_PATTERNS = [
    rf"{ROC_DATE_PATTERN}(?=[^。]{{0,12}}?公告)",
    rf"公告[^。]{{0,6}}?{ROC_DATE_PATTERN}",
    rf"最後刊登[^。]{{0,6}}?{ROC_DATE_PATTERN}",
]
# 住居所:訴願書範本的欄位名為「住址」,送達證書為「受送達人名稱姓名地址」,決定書寫「送達至…(地址:X)」
_RESIDENCE_PATTERNS = [
    # 「住居所」三字也出現在§72 的引述條文裡,故要求冒號;表單欄位名不會出現在條文,免冒號
    r"住居所[:：]\s*([^\n，。]{4,40})",
    r"(?:住址|受送達人名稱姓名地址)[:：]?\s*([^\n，。]{4,40})",
    r"[（(]\s*地址[:：]\s*([^）)]{4,40})[）)]",
    r"送達(?:至|於)[^。]{0,12}?[（(]([^）)]{4,40})[）)]",
    # 表格版面欄名與值分列輸出,欄名旁抓不到值,故另以整行地址形態直接比對
    r"(?m)^[ \t]*([^\n]{0,3}?[縣市][^\n]{0,8}?[區鄉鎮][^\n]{2,30})[ \t]*$",
]
# 住居所只需辨出縣市與行政區(查在途期間對照表用)。缺其一者不是地址,而是表格裡相鄰的
# 欄名(如「聯絡電話」)——PyMuPDF 先輸出整列欄名再輸出整列值,不驗形就會抽到隔壁欄。
_ADDRESS_CITY = re.compile(r"[縣市]")
_ADDRESS_DETAIL = re.compile(r"[區鄉鎮村里路街段巷弄號樓]")


class DeadlineFacts(BaseModel):
    """期間計算的輸入,以及卷內自述的起算日與末日(stated_*,供對帳,不參與計算)。"""

    service_date: date
    transit_days: Optional[int] = None  # None 代表卷內未載,呼叫端須查在途期間對照表
    period_days: int = _DEFAULT_PERIOD_DAYS
    filed_date: Optional[date] = None
    residence: str = ""  # 住居所原文,供查在途期間對照表
    public_notice: bool = False  # 公示送達:生效日算法未經語料驗證,結論一律待人工確認
    stated_start_date: Optional[date] = None
    stated_due_date: Optional[date] = None
    # 分槽抽取專用:送達證書槽抽不到時退用訴願人自述日,此欄記下這件事讓呼叫端能標 review_note。
    # extract_deadline_facts(單一字串) 永遠是 False——它不知道日期出自哪一份文件,無從判斷。
    service_date_self_reported: bool = False
    # 訴願書自述的收受或知悉日與送達證書不符時記在此。送達生效日仍以送達證書為準
    # (公文書推定真正),但這正是 77(2) 的爭點,故結論不得逕採,由呼叫端標註。
    disputed_receipt_date: Optional[date] = None
    # 退用自述日的原因,三態各自的 review_note 文字不同(見 pipeline._caveats),不可共用一句:
    # "missing_field"=送達證書有文字但抽不到送達時間欄;"unreadable"=送達證書文字無法辨識
    # (可讀字元比例過低,與 Ticket 4 的 OCR 門檻共用同一套判準);"absent_slot"=槽內確實沒有這份文書。
    # None 代表 service_date_self_reported=False,不適用。
    service_fallback_reason: Optional[Literal["missing_field", "unreadable", "absent_slot"]] = None


class DeadlineExtraction(BaseModel):
    """facts 為 None 時 problem 必有值:呼叫端據此送人工,不得自行補值。"""

    facts: Optional[DeadlineFacts] = None
    problem: str = ""


def _to_date(match: "re.Match[str]") -> Optional[date]:
    """民國年轉西元;卷內偶有錯字,轉不成日期就當沒抽到。"""
    try:
        return roc_to_date(match.group("y"), match.group("m"), match.group("d"))
    except ValueError:
        return None


def _dates(patterns: list[str], text: str) -> list[date]:
    """各式寫法抽到的日期去重後仍不只一個,代表卷內真有多個,交由呼叫端判為不明確。"""
    found: list[date] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = _to_date(match)
            if value is not None and value not in found:
                found.append(value)
    return found


def _transit_days(text: str) -> Optional[int]:
    days = {int(m.group(1)) for p in _TRANSIT_DAYS_PATTERNS for m in re.finditer(p, text)}
    if len(days) == 1:
        return days.pop()
    if not days and _TRANSIT_NONE.search(text):
        return 0
    return None


def _stated(text: str) -> tuple[Optional[date], Optional[date]]:
    """卷內自述的起算日與末日;末日取認定段最後出現的日期,順延後的日期恆寫在該段最末。"""
    match = _START.search(text)
    if match is None:
        return None, None
    start = _to_date(match)
    tail = text[match.end() : match.end() + _STATEMENT_SPAN]
    end = _STATEMENT_END.search(tail)
    # 此處不可去重:「至 3/4 屆滿(原末日 3/3…順延至 3/4)」去重後末筆會變成 3/3
    dates = [_to_date(m) for m in re.finditer(ROC_DATE_PATTERN, tail[: end.start()] if end else tail)]
    return start, dates[-1] if dates else None


def _service_date(text: str) -> tuple[Optional[date], bool, str]:
    """回傳(送達生效日, 是否公示送達, 問題)。公示送達改抽公告日,再依§81 換算生效日。"""
    if _PUBLIC_NOTICE.search(text):
        posted = _dates(_POSTED_PATTERNS, text)
        if len(posted) != 1:
            return None, True, f"公示送達之公告日無法認定:抽到 {len(posted)} 個"
        if _PUBLIC_NOTICE_VARIANT.search(text):
            return None, True, "公示送達之種類無法認定:卷內同時出現§78 I③或§79 之文字,等待期非 20 日"
        return public_notice_service_date(posted[0]), True, ""

    service = _dates(_SERVICE_PATTERNS, text)
    if len(service) != 1:
        return None, False, f"送達日無法認定:抽到 {len(service)} 個"
    return service[0], False, ""


def _filed_date(text: str) -> Optional[date]:
    """收文戳在就以它為準;沒有才退到敘述性寫法。兩層都是抽到不只一個即視為不明確。"""
    for patterns in (_FILED_STAMP_PATTERNS, _FILED_PATTERNS):
        found = _dates(patterns, text)
        if len(found) == 1:
            return found[0]
        if found:
            return None  # 同一層抽到多個即為不明確,不得往下層找一個看起來合理的頂替
    return None


def _residence(text: str) -> str:
    for pattern in _RESIDENCE_PATTERNS:
        for found in re.finditer(pattern, text):
            value = found.group(1).strip()
            if _ADDRESS_CITY.search(value) and _ADDRESS_DETAIL.search(value):
                return value
    return ""


def _service_date_from_certificate(text: str) -> tuple[Optional[date], bool, str]:
    """送達證書槽專用:只用送達證書會出現的寫法抽送達日,不吃訴願書自述的「收受」措辭。
    公示送達邏輯與 _service_date 共用,因為公示送達的公告事實理論上也記在送達證書上。"""
    if _PUBLIC_NOTICE.search(text):
        posted = _dates(_POSTED_PATTERNS, text)
        if len(posted) != 1:
            return None, True, f"公示送達之公告日無法認定:抽到 {len(posted)} 個"
        if _PUBLIC_NOTICE_VARIANT.search(text):
            return None, True, "公示送達之種類無法認定:卷內同時出現§78 I③或§79 之文字,等待期非 20 日"
        return public_notice_service_date(posted[0]), True, ""

    service = _dates(_SERVICE_CERT_PATTERNS, text)
    if len(service) != 1:
        return None, False, f"送達日無法認定:抽到 {len(service)} 個"
    return service[0], False, ""


def _receipt_date_from_appeal(text: str) -> Optional[date]:
    """訴願書槽專用:只認官方範本的「收受或知悉行政處分日期」欄位,不採其餘泛用寫法。"""
    dates = _dates([_RECEIPT_DATE_PATTERN], text)
    return dates[0] if len(dates) == 1 else None


def extract_from_documents(appeal_text: str, service_text: str) -> DeadlineExtraction:
    """分槽版:service_date 只信送達證書槽,receipt_date/filed_date/在途期間/residence 只信
    訴願書槽,不像 extract_deadline_facts(text) 那樣把單一字串裡的各種寫法都收進來——
    因為分槽時「這句話出自哪一份文件」本身就是抽取線索,不該再靠泛用 pattern 硬猜。

    兩份文件對送達日的自述若不一致,problem 要明講「不符」,不能跟「抽不到」混在一起——
    那是完全不同的處理路徑:前者是卷宗本身有爭點待人工認定,後者是純粹沒寫。
    """
    service_date, public_notice, service_problem = _service_date_from_certificate(service_text)
    receipt_date = _receipt_date_from_appeal(appeal_text)

    disputed = (
        receipt_date
        if service_date is not None and receipt_date is not None and service_date != receipt_date
        else None
    )

    self_reported = False
    fallback_reason: Optional[Literal["missing_field", "unreadable", "absent_slot"]] = None
    if service_date is None and receipt_date is not None:
        # 送達證書槽抽不到,但原因不只一種,呼叫端要能分開講,不能共用一句話:
        # 槽內完全沒有文字 -> 卷內確實沒有這份文書;有文字但可讀字元比例過低 -> 無法辨識,
        # 須人工調閱原件;有文字、可讀,但就是抽不到送達時間欄 -> 未載送達時間。
        if not service_text.strip():
            fallback_reason = "absent_slot"
        elif is_unreadable(service_text):
            fallback_reason = "unreadable"
        else:
            fallback_reason = "missing_field"
        service_date = receipt_date
        public_notice = False
        self_reported = True
    elif service_date is None:
        return DeadlineExtraction(problem=f"期間未計算,{service_problem or '送達日無法認定'}")

    transit = _transit_days(appeal_text)
    filed = _filed_date(appeal_text)
    correction = _CORRECTION_PERIOD.search(appeal_text)
    stated_start, stated_due = _stated(appeal_text)
    return DeadlineExtraction(
        facts=DeadlineFacts(
            service_date=service_date,
            transit_days=transit,
            residence=_residence(appeal_text),
            public_notice=public_notice,
            period_days=int(correction.group(1)) if correction else _DEFAULT_PERIOD_DAYS,
            filed_date=filed,
            stated_start_date=stated_start,
            stated_due_date=stated_due,
            service_date_self_reported=self_reported,
            service_fallback_reason=fallback_reason,
            disputed_receipt_date=disputed,
        )
    )


def extract_deadline_facts(text: str) -> DeadlineExtraction:
    """卷內文字 -> 期間事實。送達日不明確即整筆不採,寧可沒有也不要算錯。"""
    service, public_notice, problem = _service_date(text)
    if service is None:
        return DeadlineExtraction(problem=problem)

    transit = _transit_days(text)
    filed = _filed_date(text)
    correction = _CORRECTION_PERIOD.search(text)
    stated_start, stated_due = _stated(text)
    return DeadlineExtraction(
        facts=DeadlineFacts(
            service_date=service,
            transit_days=transit,
            residence=_residence(text),
            public_notice=public_notice,
            period_days=int(correction.group(1)) if correction else _DEFAULT_PERIOD_DAYS,
            filed_date=filed,
            stated_start_date=stated_start,
            stated_due_date=stated_due,
        )
    )
