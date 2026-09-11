"""訴願書的「事實」「理由」兩段:照文件自己的標題切,不靠模型分。

模型分段不穩定(常把論點歸到爭點、理由留空,prompt 寫法無法可靠阻止),
但訴願書格式本身就有這兩個標題,規則切得準而且是逐字照抄——法律文書不該被模型改寫。
"""
from app.appeal_sections import split_appeal_sections

_REAL = """訴願請求事項：請求撤銷原處分。
事    實
一、訴願人於中華民國114 年6 月27 日12 時40 分許駕駛車號RQD-3157 號汽車（下稱
系爭車輛）行經新北市三峽區岱漪南路186 號旁。
二、原處分機關認訴願人於上開時、地隨地拋棄煙蒂，致污染環境，違反廢棄物清理法
第27 條第1 款規定。
三、系爭裁處書於中華民國114 年9 月18 日送達訴願人住所，訴願人於同日收受。
理    由
一、原處分機關所憑照片未能清楚呈現所謂「煙蒂」存在之證據：

  卷附稽查照片僅能辨識車輛或人員位置。
二、訴願人當時並未有丟棄煙蒂之行為：
  訴願人當時雖有抽煙，惟已將煙蒂拿回車上垃圾桶丟棄。
三、綜上，原處分機關舉證不足且認定事實有誤，請求撤銷原處分。
附送證件
名稱字號
"""


def test_splits_a_real_appeal_into_facts_and_reasons():
    facts, reasons = split_appeal_sections(_REAL)

    assert len(facts) == 3
    assert facts[0].startswith("訴願人於中華民國114")
    assert "岱漪南路186 號旁。" in facts[0]  # 跨行的內容要接回來
    assert len(reasons) == 3
    assert reasons[0].startswith("原處分機關所憑照片")
    assert "卷附稽查照片僅能辨識車輛或人員位置。" in reasons[0]  # 條號下的縮排段落屬於該條
    assert reasons[2].startswith("綜上")


def test_the_trailing_sections_are_not_swallowed_into_the_reasons():
    """「附送證件」之後是附件清單,不是理由;吃進來會讓最後一條理由多出一堆表格文字。"""
    _, reasons = split_appeal_sections(_REAL)
    assert not any("附送證件" in r or "名稱字號" in r for r in reasons)


def test_headings_written_without_spaces_also_match():
    text = "事實\n一、甲事。\n二、乙事。\n理由\n一、丙由。\n此  致\n"
    facts, reasons = split_appeal_sections(text)
    assert facts == ["甲事。", "乙事。"]
    assert reasons == ["丙由。"]


def test_a_document_without_both_headings_yields_nothing():
    """只有其中一個標題、或兩個都沒有時一律回空——寧可讓模型的結果留著,
    也不要拿一個切錯的結果去覆蓋它。"""
    for text in (
        "一、訴願人主張原處分違法。",  # 沒有標題
        "事    實\n一、甲事。\n",  # 只有事實
        "理    由\n一、丙由。\n",  # 只有理由
        "",
    ):
        assert split_appeal_sections(text) == ([], [])


def test_a_heading_with_no_numbered_items_yields_nothing():
    """標題在、底下卻沒有條列(或整段空白)時同樣不採用:切出空清單等於把欄位清空。"""
    text = "事    實\n\n理    由\n一、丙由。\n"
    assert split_appeal_sections(text) == ([], [])


def test_an_answer_document_is_not_mistaken_for_an_appeal():
    """訴願答辯書也有「事 實」「理 由」兩個標題,但那是機關的說法,不是訴願人的。
    呼叫端只餵訴願書槽,這裡再驗一次:誤用會把機關主張填進訴願人的欄位。"""
    answer = "新北市政府環境保護局 訴願答辯書\n答 辯 聲 明\n訴願駁回。\n事    實\n一、甲事。\n理    由\n一、乙由。\n"
    assert split_appeal_sections(answer) == ([], [])


# ---------- 接進 pipeline:文件切得出來就以文件為準 ----------


def _case_with_appeal(appeal_text: str):
    from app.models import Case, CaseDocument, DocumentCheck

    return Case(
        case_id="c-split001",
        created_at="2026-08-17T00:00:00+00:00",
        title="測試",
        source="pdf",
        input_text=appeal_text,
        documents={
            "appeal": CaseDocument(
                slot="appeal",
                source="pdf",
                text=appeal_text,
                check=DocumentCheck(matched=True, method="rule"),
            )
        },
    )


def _info(**overrides):
    from app.models import CaseInfo

    base = dict(
        appellant="鄭婉芳",
        agency="新北市政府環境保護局",
        disposition_date="114年9月16日",
        disposition_no="新北環稽字第41-114-090351號",
        disposition_summary="裁處罰鍰",
        case_type="廢棄物清理法",
    )
    base.update(overrides)
    return CaseInfo(**base)


def test_the_document_headings_win_over_the_model_split():
    """模型常把論點全歸到爭點、理由留空;文件自己有標題時以文件為準,
    而且是逐字照抄,不是模型改寫過的版本。"""
    from app.pipeline import apply_appeal_sections

    case = _case_with_appeal(_REAL)
    info = _info(appeal_facts=["模型亂塞的一整包"], appeal_reasons=[])

    result = apply_appeal_sections(info, case)

    assert len(result.appeal_facts) == 3
    assert len(result.appeal_reasons) == 3
    assert result.appeal_reasons[2].startswith("綜上")


def test_a_document_without_headings_keeps_the_model_output():
    from app.pipeline import apply_appeal_sections

    case = _case_with_appeal("一、訴願人主張原處分違法,請求撤銷。")
    info = _info(appeal_facts=["模型抽的事實"], appeal_reasons=["模型抽的理由"])

    result = apply_appeal_sections(info, case)

    assert result.appeal_facts == ["模型抽的事實"]
    assert result.appeal_reasons == ["模型抽的理由"]


def test_a_case_without_an_appeal_slot_is_left_alone():
    """文字輸入建的案件沒有分槽;沒有訴願書槽就沒有可切的來源,不得拿合併字串硬切
    ——那裡面還有送達證書與原處分書,兩者也有「事實」「理由」字樣。"""
    from app.models import Case
    from app.pipeline import apply_appeal_sections

    case = Case(
        case_id="c-split002",
        created_at="2026-08-17T00:00:00+00:00",
        title="測試",
        source="text",
        input_text=_REAL,
    )
    info = _info(appeal_reasons=["模型抽的理由"])

    assert apply_appeal_sections(info, case).appeal_reasons == ["模型抽的理由"]
