"""案件處理 pipeline:F1 -> screening -> F3 -> (F2 ->) F2+ -> F4,逐階段落庫。"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.config import settings
from app.deadline import (
    APPEAL_PERIOD_DAYS,
    DeadlineResult,
    compute_deadline,
    compute_one_year_deadline,
    format_roc,
    fullwidth,
)
from app.deadline_extract import DeadlineExtraction, DeadlineFacts, extract_deadline_facts, extract_from_documents
from app.holidays import covered_years, load_holidays
from app.notice_clause import NoticePeriodRule, classify_notice_clause
from app.procedural_checks import (
    apply_article_77_1,
    apply_article_77_3,
    check_required_fields,
    check_standing,
    resolve_standing_assessment,
)
from app.transit import resolve_transit_days
from app.models import (
    DOCUMENT_SLOT_LABELS,
    OCR_DATE_FIELD_BY_SLOT,
    Case,
    CaseInfo,
    DeadlineCheck,
    DraftResult,
    ReanalyzeFrom,
    ScreeningResult,
    join_review_notes,
    parse_clause,
)
from app.appeal_sections import split_appeal_sections
from app.dates import normalize_case_info_dates, parse_roc
from app.decision_header import decision_header_defaults
from app.pdf_render import decision_plain_text
from app.providers.base import AIProvider
from app.store import CaseStore

_F3_TOP_K = 3  # F3 呈現上限;provider.find_similar_cases 回傳的 25 件重排候選只落前 3 件到 f3
_INADMISSIBLE_MAIN_TEXT = "訴願不受理。"  # 語料 90/90 件不受理決定書主文逐字相同,不交由模型決定
_APPEAL_ACT_ARTICLE = 77
_OVERDUE_CLAUSE = "77條第2款"
_OVERDUE_CLAUSE_KEY = parse_clause(_OVERDUE_CLAUSE)  # 兩者同一件事,不手動同步
# §77(5) 語料 0 件,模型若判這款不得逕採——沒有語料支撐的款次,連引哪些法條都答不出來
_SUPPORTED_CLAUSES = {1, 2, 3, 4, 6, 7, 8}

# 各款附加條文取自語料統計:該款出現率 >=50% 且至少 2 件(件數下限防單一案例把比率拉到 50%)
_CLAUSE_LAW_KEYS = {
    1: ["訴願法#56", "訴願法#47", "行政訴訟法#67", "行政訴訟法#71"],
    2: ["訴願法#14", "行政程序法#72"],
    3: ["訴願法#1", "訴願法#18"],
    4: ["訴願法#19", "行政程序法#72", "行政程序法#74", "民法#12"],
    6: ["訴願法#1"],
    7: [],
    8: ["訴願法#3"],
}


def inadmissible_law_keys(matched_clause: str | None) -> list[str]:
    """不受理案的可引用法條:訴願法§77 恆在,再依款次補該款實際會引的條文。"""
    parsed = parse_clause(matched_clause)
    extra = _CLAUSE_LAW_KEYS.get(parsed[1]) if parsed and parsed[0] == _APPEAL_ACT_ARTICLE else None
    return list(dict.fromkeys(["訴願法#77"] + (extra or [])))


def apply_appeal_sections(info: CaseInfo, case) -> CaseInfo:
    """訴願書自帶「事 實」「理 由」兩個標題時,兩欄改以文件原文為準。

    模型分段不穩定(常把論點歸到爭點、理由留空,prompt 寫法無法可靠阻止),
    但格式本身切得準,而且切出來是逐字照抄——法律文書不該被模型改寫。只讀訴願書槽:
    合併字串裡還有送達證書與原處分書,兩者也有「事實」「理由」字樣。切不出來就留模型的結果。
    """
    appeal = case.documents.get("appeal") if case.documents else None
    facts, reasons = split_appeal_sections(appeal.text if appeal else "")
    if not (facts and reasons):
        return info
    return info.model_copy(update={"appeal_facts": facts, "appeal_reasons": reasons})


def guard_contradictory_screening(screening: ScreeningResult) -> ScreeningResult:
    """模型答「通過」卻同時指出不受理款次時,以款次為準:具體發現優於概括結論,原樣放行
    等於讓承辦人看到一個寫著不受理事由的「受理」。款次欄填非款次文字(「無」「不適用」)
    不算矛盾,那不是發現,不得據以翻掉受理結論。"""
    if not screening.passed or parse_clause(screening.matched_clause) is None:
        return screening
    note = f"程序審查結論與款次矛盾（判通過卻指出{screening.matched_clause}），已以款次為準，須人工確認"
    return screening.model_copy(
        update={"passed": False, "review_note": join_review_notes(screening.review_note, note)}
    )


def guard_unsupported_clause(screening: ScreeningResult) -> ScreeningResult:
    """§77(5) 語料 0 件;模型若判這款,或款次解析不出來,一律標記待人工認定,不逕採——
    沒有語料就答不出該款次實際會引哪些法條,寧可讓承辦人員自己判,不要讓系統裝出一個
    沒有根據的結論。只影響不受理判斷(passed=True 不動)。"""
    if screening.passed:
        return screening
    parsed = parse_clause(screening.matched_clause)
    if parsed and parsed[0] == _APPEAL_ACT_ARTICLE and parsed[1] in _SUPPORTED_CLAUSES:
        return screening
    if parsed and parsed[0] == _APPEAL_ACT_ARTICLE:
        note = f"本版不判訴願法第{parsed[1]}款，須人工認定"
    else:
        note = "訴願法款次解析不出，須人工認定"
    return screening.model_copy(update={"review_note": join_review_notes(screening.review_note, note)})


def enforce_inadmissible_format(draft: DraftResult, screening: ScreeningResult) -> DraftResult:
    """不受理決定書體例:主文為固定套語、事實欄依訴願法第89條第1項第3款不記載。
    部分不受理部分駁回是唯一例外——主文逐標的分項、事實欄留給駁回的部分,固定套語套上去就錯。"""
    if screening.passed or draft.draft_type == "部分不受理部分駁回":
        return draft
    return draft.model_copy(
        update={"draft_type": "不受理", "main_text": _INADMISSIBLE_MAIN_TEXT, "fact": ""}
    )


_SERVICE_FALLBACK_NOTES = {
    # 三種狀況原因不同、該做的事也不同,不可共用一句「未經送達證書核對」——
    # 那句話會讓承辦人誤以為只是流程沒走完。
    "missing_field": "送達證書未載送達時間，送達生效日採訴願人自述",
    "unreadable": "送達證書無法辨識，送達生效日採訴願人自述，須人工調閱原件",
    "absent_slot": "卷內無送達證書，送達生效日採訴願人自述",
}


def _caveats(facts: DeadlineFacts, due_date: date | None) -> list[str]:
    """結論本身可能被推翻的原因;非空即不得逕採。due_date 為 None(期間未算出)時
    只回與事實有關的那幾項,末日相關的兩項無從判斷。"""
    notes = []
    if facts.public_notice:
        notes.append("公示送達生效日之算法未經語料驗證，須人工確認")
    if facts.service_date_self_reported:
        notes.append(_SERVICE_FALLBACK_NOTES.get(facts.service_fallback_reason, "送達生效日採訴願人自述，未經送達證書核對"))
    if facts.disputed_receipt_date is not None and facts.service_date_self_reported:
        notes.append(
            f"訴願書自述收受或知悉日{format_roc(facts.disputed_receipt_date)}與送達生效日不符，"
            "而生效日本身出自訴願人自述、未經送達證書核對，結論須人工認定"
        )
    if due_date is None:
        return notes
    if due_date.year not in covered_years():
        notes.append(f"{due_date.year} 年國定假日未收錄，末日是否須順延未經計入")
    return notes


def _advisories(facts: DeadlineFacts, due_date: date | None) -> list[str]:
    """要讓承辦人看到、但不阻擋算式覆寫的歧異:兩項都是對造的法律主張,不是我方抽錯的徵兆。

    送達生效日以送達證書為準是行政程序法§72-74 的定論,訴願人主張較晚知悉不延長起算;
    算式已據此挑定送達證書,再以「有爭點」為由拒絕採用自己的結論就是算了不算。
    卷內自述的末日同理——訴願人主張的起算日與我方不同時,末日本來就會不一樣。
    送達證書缺席而生效日出自自述時,另由 _caveats 擋下。
    """
    notes = []
    if facts.disputed_receipt_date is not None and not facts.service_date_self_reported:
        notes.append(
            f"訴願書自述收受或知悉日{format_roc(facts.disputed_receipt_date)}與送達證書不符，"
            "送達生效日依法採送達證書；自述日不影響起算，惟送達合法性如有爭執仍須人工認定"
        )
    if due_date is not None and facts.stated_due_date is not None and facts.stated_due_date != due_date:
        notes.append(
            f"算得末日與卷內自述之{format_roc(facts.stated_due_date)}不符，自述末日係訴願人之主張，須人工確認"
        )
    return notes


def _with_filed_date(
    facts: DeadlineFacts, result: DeadlineResult, detail: str, extra_notes: list[str]
) -> DeadlineCheck:
    """算出末日之後的共同收尾:對帳註記 + 收文日比對。各期間分支只負責算末日與敘述。"""
    caveats = [note for note in extra_notes if note] + _caveats(facts, result.due_date)
    advisories = _advisories(facts, result.due_date)
    if facts.filed_date is None:
        return DeadlineCheck(
            service_date=facts.service_date,
            due_date=result.due_date,
            detail=f"{detail}。",
            review_note=";".join(["卷內未載機關收文日，無從認定是否逾期"] + caveats + advisories),
            override_blocked=True,
        )

    overdue = result.is_overdue(facts.filed_date)
    return DeadlineCheck(
        overdue=overdue,
        service_date=facts.service_date,
        due_date=result.due_date,
        filed_date=facts.filed_date,
        detail=f"{detail}，機關收文日{format_roc(facts.filed_date)}，{'已逾期' if overdue else '未逾期'}。",
        review_note=";".join(caveats + advisories),
        override_blocked=bool(caveats),
    )


def _check_deadline_from_extraction(
    extraction: DeadlineExtraction, notice: NoticePeriodRule | None = None
) -> DeadlineCheck:
    """抽取結果(不論來自單一字串或分槽) -> 訴願期間認定。抽不到事實就說明抽不到,不以預設值頂替。

    notice 為教示條款的認定(行政程序法§98,見 notice_clause.py);None 代表未經檢核,
    行為為照訴願法§14 的 30 日算,單一字串入口即走這條。
    """
    if extraction.facts is None:
        return DeadlineCheck(review_note=f"期間未計算，{extraction.problem}")

    facts = extraction.facts
    rule = notice or NoticePeriodRule()
    prefix = f"{rule.finding}。" if rule.finding else ""

    if rule.basis == "undetermined":
        # 教示條款有錯而§98 分支定不出來:此時法定期間算式的方向不利人民(期間可能其實是一年,
        # 或應自更正通知送達之翌日重新起算),故不給末日也不給逾期結論,只交出送達生效日與原因。
        return DeadlineCheck(
            service_date=facts.service_date,
            detail=f"{prefix}送達生效日{format_roc(facts.service_date)}。",
            review_note=";".join([f"期間未計算，{rule.review_note}"] + _caveats(facts, None)),
        )

    if rule.basis == "one_year":
        # 在途期間(訴願法§16)是訴願期間的扣除項;§98 III 的一年是「視為於法定期間內所為」的
        # 保護上限,語料無一件據以加計在途期間,故不加——也因此這一支不必卡在途期間查表。
        result = compute_one_year_deadline(facts.service_date, holidays=load_holidays())
        detail = (
            f"{prefix}送達生效日{format_roc(facts.service_date)}，起算日{format_roc(result.start_date)}，"
            f"依行政程序法第９８條第３項期間一年，末日{format_roc(result.due_date)}"
        )
        return _with_filed_date(facts, result, detail, [rule.review_note])

    # 卷內未載在途期間時改查對照表(訴願扣除在途期間辦法附表),查不到就不算,不以 0 日頂替
    transit = facts.transit_days
    lookup_note = ""
    if transit is None:
        lookup = resolve_transit_days(facts.residence, settings.APPEAL_AGENCY_LOCATION)
        transit, lookup_note = lookup.days, lookup.review_note
    if transit is None:
        # 查表失敗的原因分兩種:住居所不在附表內(無 lookup_note),或同一直轄市各組日數不同
        # 而行政區被遮罩(lookup_note 講得出差幾日)。後者承辦人補一個行政區就算得出來,
        # 混成同一句「無法由住居所認定」會讓可救的案子看起來也沒救。
        reason = lookup_note or "在途期間卷內未載且無法由住居所查表認定"
        return DeadlineCheck(review_note=f"期間未計算，{reason}")

    if rule.basis == "restart_from_correction":
        # §98 I:自更正通知送達之翌日起算法定期間,起點換成更正通知的送達日,期間仍為法定期間
        result = compute_deadline(
            rule.correction_service_date,
            holidays=load_holidays(),
            period_days=APPEAL_PERIOD_DAYS,
            transit_days=transit,
        )
        detail = (
            f"{prefix}更正通知送達日{format_roc(rule.correction_service_date)}，"
            f"起算日{format_roc(result.start_date)}，依行政程序法第９８條第１項期間"
            f"{fullwidth(APPEAL_PERIOD_DAYS)}日、在途{fullwidth(transit)}日，末日{format_roc(result.due_date)}"
        )
        return _with_filed_date(facts, result, detail, [rule.review_note])

    # statutory / not_assessed / stated_longer:起點同為送達生效日,只有期間長度不同。
    # stated_longer 取教示所載期間而非 facts.period_days——後者的非預設值來自「補正期間 N 日」,
    # 那是訴願法§62 的補正,與原處分教示無關,兩者不會同時出現在同一份卷內。
    period_days = rule.stated_days if rule.basis == "stated_longer" else facts.period_days
    result = compute_deadline(
        facts.service_date,
        holidays=load_holidays(),
        period_days=period_days,
        transit_days=transit,
    )
    detail = (
        f"{prefix}送達生效日{format_roc(facts.service_date)}，起算日{format_roc(result.start_date)}，"
        f"期間{fullwidth(period_days)}日、在途{fullwidth(transit)}日，末日{format_roc(result.due_date)}"
    )
    notes = [rule.review_note]
    if rule.basis == "stated_longer" and facts.filed_date is not None and result.is_overdue(facts.filed_date):
        # §98 II 的保護以「於原告知之期間內為之」為要件;連告知的較長期間都逾越時本項不適用,
        # 但此時仍可能落入同條第3項的一年,故不得以此逕認逾期。
        notes.append(
            "訴願人未於原告知之期間內提起，行政程序法第９８條第２項不適用，"
            "是否落入同條第３項之一年期間須人工認定"
        )
    return _with_filed_date(facts, result, detail, notes)


def check_deadline(text: str) -> DeadlineCheck:
    """單一字串版,語料測試(對決定書理由全文)沿用此入口;行為原樣不動。"""
    return _check_deadline_from_extraction(extract_deadline_facts(text))


# 送達證書缺件時唯一剩下的送達日出自訴願人自述,據以算出的逾期足以把案件打成不受理,
# 而卷內沒有任何公文書可以核對它;此流程一律視為未逾期,是否逾期留給人工調卷認定。
_ABSENT_SERVICE_NOTE = "卷內無送達證書，未計算期間，本流程視為未逾期，是否逾期須人工確認"


def check_deadline_from_case(case: Case, info: CaseInfo | None = None) -> DeadlineCheck:
    """pipeline 用的入口:分槽讀 case.documents,service_date 只信送達證書槽、
    其餘只信訴願書槽,兩槽不一致時 extract_from_documents 會明講「不符」而非「抽不到」。

    info 是本輪 F1 的擷取結果(教示條款欄在裡面)。run_case 必須顯式傳入——它手上的 case
    是 F1 之前讀出來的快照,case.f1 還是 None,靠預設值會靜默漏掉整個§98 檢核。

    送達證書槽空白即不計算期間,見 _ABSENT_SERVICE_NOTE。
    """
    appeal_text = case.documents["appeal"].text if "appeal" in case.documents else ""
    service_text = case.documents["service"].text if "service" in case.documents else ""
    disposition_text = case.documents["disposition"].text if "disposition" in case.documents else ""
    if not service_text.strip():
        return DeadlineCheck(overdue=False, review_note=_ABSENT_SERVICE_NOTE)
    case_info = info or case.f1
    notice = classify_notice_clause(
        case_info.disposition_notice_clause if case_info else "",
        disposition_text,
        case_text=f"{appeal_text}\n{disposition_text}",
    )
    edited = _edited_deadline_dates(case, case_info)
    extraction = extract_from_documents(
        appeal_text,
        service_text,
        service_date=edited.service_date,
        filed_date=edited.filed_date,
    )
    check = _check_deadline_from_extraction(extraction, notice)
    if edited.adopted and check.detail:
        check = check.model_copy(update={"detail": f"{'、'.join(edited.adopted)}依承辦人修正之案件資訊計算。{check.detail}"})
    if edited.unreadable:
        note = f"案件資訊{'、'.join(edited.unreadable)}無法辨識，仍依卷內文件計算"
        check = check.model_copy(update={"review_note": join_review_notes(check.review_note, note)})
    return _flag_ocr_slots(case, check)


@dataclass
class _EditedDates:
    service_date: Optional[date] = None
    filed_date: Optional[date] = None
    adopted: list[str] = field(default_factory=list)  # 採用了哪些承辦人修正的日期,寫進 detail 交代來源
    unreadable: list[str] = field(default_factory=list)  # 改了但解析不出的欄位,寫進 review_note 而非默默沿用卷內值


def _edited_deadline_dates(case: Case, info: CaseInfo | None) -> _EditedDates:
    """承辦人改過的 F1 日期才覆寫卷內抽取值;基準是模型原判(f1_system,未改過時即 f1)。
    模型剛擷取的日期不是人核定的:run_case 進來時 case.f1 尚為 None、重跑時 info 就是 case.f1,皆視為未改。"""
    baseline = case.f1_system or case.f1
    edited = _EditedDates()
    if info is None or baseline is None:
        return edited

    def edited_date(name: str, label: str) -> Optional[date]:
        value = getattr(info, name)
        if not value or value == getattr(baseline, name):
            return None
        parsed = parse_roc(value)
        (edited.adopted if parsed else edited.unreadable).append(label)
        return parsed

    edited.service_date = edited_date("service_date", "送達日期")
    edited.filed_date = edited_date("appeal_filed_date", "機關收文日")
    return edited
    for field, target, label in _DEADLINE_F1_FIELDS:
        value = getattr(info, field)
        if not value or value == getattr(baseline, field):
            continue
        parsed = parse_roc(value)
        if parsed is None:
            edited.unreadable.append(label)
            continue
        edited.labels.append(label)
        setattr(edited, target, parsed)
    return edited


def _flag_ocr_slots(case: Case, check: DeadlineCheck) -> DeadlineCheck:
    """經 OCR 取得文字的槽,其日期預設不得據以覆寫程序審查:模型抽字會編字,而這套系統的
    正確性建立在日期上。承辦人若已核對過該槽的關鍵日期(f1 與 f1_system 對應欄位不同,
    見 models.OCR_DATE_FIELD_BY_SLOT),解除該槽的阻擋;沒改過的槽維持阻擋。"""
    ocr_slots = [slot for slot, doc in case.documents.items() if doc.ocr]
    if not ocr_slots:
        return check

    blocked_labels, confirmed_labels = [], []
    for slot in ocr_slots:
        field = OCR_DATE_FIELD_BY_SLOT.get(slot)
        confirmed = (
            field is not None
            and case.f1 is not None
            and case.f1_system is not None
            and getattr(case.f1, field) != getattr(case.f1_system, field)
        )
        (confirmed_labels if confirmed else blocked_labels).append(DOCUMENT_SLOT_LABELS[slot])

    notes = []
    if blocked_labels:
        notes.append(f"{'、'.join(blocked_labels)}文字由 OCR 取得，日期須人工核對原件")
    notes.extend(f"{label}日期已由承辦人核對" for label in confirmed_labels)

    return check.model_copy(
        update={
            "review_note": ";".join(n for n in (check.review_note, *notes) if n),
            "override_blocked": check.override_blocked or bool(blocked_labels),
        }
    )


def reconcile_deadline(
    screening: ScreeningResult, check: DeadlineCheck
) -> tuple[ScreeningResult, DeadlineCheck]:
    """算得出逾期即以算式取代模型判斷;結論待確認或與模型相反時兩者都不動,只記歧異待人工。"""
    if check.overdue is True and check.override_blocked:
        # 算式本身還要人工確認,就不該拿去覆寫審查結果,更不該進草稿理由;但被質疑的是
        # 「本案未逾期」這個結論,註記只落在期間欄的話,單看程序審查區塊的人會把它當可逕採
        note = f"期間算得出逾期但{check.review_note}，結論未經期間算式確認"
        return (
            screening.model_copy(
                update={"review_note": join_review_notes(screening.review_note, note)}
            ),
            check.model_copy(
                update={"review_note": join_review_notes(check.review_note, "未據以覆寫程序審查")}
            ),
        )
    if check.overdue is True:
        reasoning = f"{check.detail}依訴願法第77條第2款應不受理。程序審查意見：{screening.reasoning}"
        update = {"passed": False, "matched_clause": _OVERDUE_CLAUSE, "reasoning": reasoning}
        return screening.model_copy(update=update), check
    if check.overdue is False and parse_clause(screening.matched_clause) == _OVERDUE_CLAUSE_KEY:
        note = "計算結果未逾期，與程序審查認定之第２款不符，須人工確認"
        # 附加而非取代:既有的 review_note 裡是算式本身的保留事項(公示送達、採自述送達日、
        # §98 期間分支),那些正是解釋歧異從何而來的線索,覆蓋掉會讓承辦人只看到結論不一致。
        merged = join_review_notes(check.review_note, note)
        if check.override_blocked:
            return screening, check.model_copy(update={"review_note": merged})
        # 算式乾淨且明說未逾期:撤銷第2款認定。算式已被授權單方面把案件打成不受理
        # (上一個分支),不讓它擋下一個它明說不成立的不受理,就是只在對機關有利的方向信任它。
        withdrawn = join_review_notes(screening.review_note, "第2款認定經期間算式否定，已撤銷，須人工確認")
        # 結論翻了理由也要跟著翻:留著模型那句「應不受理」,受理案的程序審查意見仍在說不受理,
        # 而這份 reasoning 會原樣進 F4 的提示與畫面
        retired = f"本件不以逾期論，第2款之認定已撤銷，須人工確認。程序審查意見：{screening.reasoning}"
        return (
            screening.model_copy(
                update={
                    "passed": True,
                    "matched_clause": None,
                    "reasoning": retired,
                    "review_note": withdrawn,
                }
            ),
            check.model_copy(update={"review_note": merged}),
        )
    return screening, check


def _retrieval_and_draft(
    case_id: str,
    store: CaseStore,
    provider: AIProvider,
    info: CaseInfo,
    screening: ScreeningResult,
    case_text: str,
) -> None:
    """F3/F2/F2+/F4:程序審查結論定了之後的檢索與草稿。單獨抽出來是為了讓重跑能從這裡起跑——
    曾被人工推翻的案件重跑時若又呼叫 screen_admissibility,人剛改的判斷會被模型改回去。

    順序改為 F3 -> F2 -> F2+ -> F4:F2 的候選法規來自 F3 相似案例帶出的 law_id,F3 必須先跑;
    `Stage` 的字面值不變(f2 仍在 f2_refs 之前),只有實際呼叫順序與落庫順序跟著調動。
    例外不在此處理,由呼叫端統一落 status=error。"""
    store.update(case_id, {"retrieval_input_screening": screening})
    store.update(
        case_id, {"track": "admissible" if screening.passed else "inadmissible", "current_stage": "f3"}
    )

    # F3:先找相似案例,25 件重排候選只落前 _F3_TOP_K 件,F2 的候選法規則吃全部 25 件的 law_ids
    all_cases = provider.find_similar_cases(info, screening, case_text)
    top_cases = all_cases[:_F3_TOP_K]

    if screening.passed:
        store.update(case_id, {"f3": top_cases, "current_stage": "f2"})
        # 候選 law_id = 25 件案例 law_ids 的聯集,依出現次數遞減;案例全無 law_ids 時退回全庫檢索
        counts = Counter(lid for case in all_cases for lid in case.law_ids)
        candidate_law_ids = [lid for lid, _ in counts.most_common()] or None
        laws = provider.recommend_laws(info, candidate_law_ids)
        store.update(case_id, {"f2": laws, "current_stage": "f2_refs"})
    else:
        # 不通過:跳過 F2,法源精查後只供草稿引用,不寫進 f2
        store.update(case_id, {"f3": top_cases, "current_stage": "f2_refs"})
        laws = provider.get_law_articles(inadmissible_law_keys(screening.matched_clause))

    # F2+ 參考見解:兩條 track 都跑,但不進 laws——它們沒有條號,湊不出 F4 的引用格式
    store.update(case_id, {"f2_refs": provider.find_references(info), "current_stage": "f4"})

    draft = enforce_inadmissible_format(
        provider.generate_draft(info, screening, laws, top_cases), screening
    )
    store.update(case_id, {"f4": draft})
    # 表頭預設值在落地那一刻實際寫進 decision_header,不是渲染時回落——之後承辦人改的就是這份
    store.update(case_id, {"decision_header": decision_header_defaults(store.get(case_id))})
    # 承辦人編輯與下載的是攤平後的全文,產出時就寫好;重跑會重新攤平,故先前的編輯要另存一版
    # (見 main.reanalyze_case),不是在這裡保留。
    plain = decision_plain_text(store.get(case_id))
    store.update(case_id, {"draft_plain_text": plain, "current_stage": "done", "status": "done"})


def rerun_case(case_id: str, store: CaseStore, provider: AIProvider, start: ReanalyzeFrom) -> None:
    """重跑,起跑點由呼叫端明確指定(main.reanalyze_case 已依 from 清好對應欄位並設好
    status/current_stage)——不再由歷史旗標推測。"""
    case = store.get(case_id)
    if case is None:
        raise ValueError(f"case not found: {case_id}")

    try:
        if start == "f1":
            run_case(case_id, store, provider)
            return
        if start == "screening":
            _screen_and_draft(case_id, store, provider, case, case.f1)
            return
        _retrieval_and_draft(case_id, store, provider, case.f1, case.screening, case.input_text)
    except Exception as exc:  # noqa: BLE001 - 背景任務不得中斷,錯誤要落庫讓畫面看得到
        store.update(case_id, {"status": "error", "error": str(exc)})


def _screen_and_draft(
    case_id: str, store: CaseStore, provider: AIProvider, case: Case, info: CaseInfo
) -> None:
    """程序審查 → F3/F2/F2+/F4。抽出來是為了讓「f1 已被人工修改」的案件能從這裡起跑:
    那種案件重跑時不得再呼叫 extract_case_info,否則人剛改的欄位會被模型改回去。
    例外不在此處理,由呼叫端統一落 status=error。"""
    # f1_stale 的判準依據;順便重新讀一次 case,呼叫端手上的快照可能是重跑前尚未清好欄位的舊版本
    case = store.update(case_id, {"screening_input_f1": info})
    screening = provider.screen_admissibility(info, case.input_text)
    screening = guard_contradictory_screening(screening)
    # §77(1) 必要記載檢核在期間計算之前:期間逾期是最能客觀算出的事實,若兩者都成立,
    # 讓 reconcile_deadline 的覆寫有最終發言權(與既有的期間覆寫優先順序一致)。
    # notice 目前一律傳 None——補正通知抽取尚未建立(見 procedural_checks.py 註解),
    # 這代表「查無補正通知」是誠實的預設值,不是假裝已檢查過。
    screening = apply_article_77_1(screening, check_required_fields(info), None)
    standing_check = check_standing(info)
    # 只有欄位不一致時才需要 LLM 判斷利害關係——一致或欄位空白時 check_standing
    # 已經給出結論(consistent=True 或 None),呼叫 provider 只會是白跑一趟。
    has_standing = (
        resolve_standing_assessment(provider.assess_standing(info, case.input_text))
        if standing_check.consistent is False
        else None
    )
    screening = apply_article_77_3(screening, standing_check.model_copy(update={"has_standing": has_standing}))
    # info 顯式傳入:run_case 手上的 case 是 F1 之前的快照,case.f1 仍為 None,
    # 教示條款(§98)會整段漏掉
    screening, deadline = reconcile_deadline(screening, check_deadline_from_case(case, info))
    screening = guard_unsupported_clause(screening)
    store.update(case_id, {"screening": screening, "deadline": deadline})

    _retrieval_and_draft(case_id, store, provider, info, screening, case.input_text)


def run_case(case_id: str, store: CaseStore, provider: AIProvider) -> None:
    case = store.get(case_id)
    if case is None:
        raise ValueError(f"case not found: {case_id}")

    try:
        info = normalize_case_info_dates(apply_appeal_sections(provider.extract_case_info(case.input_text), case))
        store.update(case_id, {"f1": info, "current_stage": "screening"})
        _screen_and_draft(case_id, store, provider, case, info)
    except Exception as exc:  # noqa: BLE001 - pipeline 需捕捉任何例外落庫,不中斷背景任務
        store.update(case_id, {"status": "error", "error": str(exc)})
