from app.document_check import check_document
from app.config import settings


class _FakeGeminiHTTP:
    """記錄呼叫並回傳固定 payload,模擬 Gemini generateContent 回應。"""

    def __init__(self, document_type: str, reasoning: str = ""):
        self._document_type = document_type
        self._reasoning = reasoning
        self.calls = []

    def post(self, url, params=None, json=None):
        self.calls.append((url, params, json))
        body = {"document_type": self._document_type, "reasoning": self._reasoning}
        return _FakeResponse(
            {"candidates": [{"content": {"parts": [{"text": __import__("json").dumps(body, ensure_ascii=False)}]}}]}
        )


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


# ---------- 規則判斷:強特徵直接命中 ----------


def test_rule_matches_appeal_document_by_strong_keyword():
    text = "訴願書\n訴願人:王大明\n原處分機關:彰化縣環境保護局\n請求事項:撤銷原處分。"
    check = check_document("appeal", text)
    assert check.matched is True
    assert check.method == "rule"


def test_rule_matches_service_certificate_by_strong_keyword():
    text = "送達證書\n送達時間:中華民國114年5月28日\n送達方式:寄存於派出所。"
    check = check_document("service", text)
    assert check.matched is True
    assert check.method == "rule"


def test_rule_matches_disposition_by_strong_keyword():
    text = "裁處書\n主旨:違反廢棄物清理法,處罰鍰6000元。\n事實:...\n理由:...\n教示條款:..."
    check = check_document("disposition", text)
    assert check.matched is True
    assert check.method == "rule"


# ---------- 規則判斷:文字明顯是別的槽位 ----------


def test_rule_rejects_service_certificate_uploaded_as_appeal():
    text = "送達證書\n送達時間:中華民國114年5月28日\n送達方式:寄存於派出所。"
    check = check_document("appeal", text)
    assert check.matched is False
    assert check.method == "rule"
    assert "送達證書" in check.note


# ---------- 規則判斷不出來,退到 Gemini ----------


def test_falls_back_to_gemini_when_rule_is_inconclusive(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key")
    http = _FakeGeminiHTTP("訴願書", "內容提及訴願人與請求事項")

    check = check_document("appeal", "內容含糊,規則判斷不出特徵", http_client=http)

    assert check.matched is True
    assert check.method == "gemini"
    assert len(http.calls) == 1


def test_gemini_reports_mismatch_when_document_is_a_different_type(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key")
    http = _FakeGeminiHTTP("送達證書")

    check = check_document("appeal", "內容含糊,規則判斷不出特徵", http_client=http)

    assert check.matched is False
    assert check.method == "gemini"


def test_gemini_returns_none_when_it_cannot_confirm_either(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key")
    http = _FakeGeminiHTTP("無法確認", "文字過於片段")

    check = check_document("appeal", "內容含糊,規則判斷不出特徵", http_client=http)

    assert check.matched is None
    assert check.method == "gemini"


def test_missing_gemini_key_reports_none_instead_of_calling_out(monkeypatch):
    """未設定金鑰時不得出網呼叫,直接回無法確認交人工核對。"""
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    check = check_document("appeal", "內容含糊,規則判斷不出特徵")

    assert check.matched is None
    assert check.method == "none"
    assert "Gemini" in check.note


def test_gemini_call_failure_does_not_raise(monkeypatch):
    """外部服務任何失敗都不能讓建案流程中斷,失敗一律轉成 matched=None。"""
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key")

    class _BrokenHTTP:
        def post(self, *args, **kwargs):
            raise RuntimeError("連線逾時")

    check = check_document("appeal", "內容含糊,規則判斷不出特徵", http_client=_BrokenHTTP())

    assert check.matched is None
    assert check.method == "none"
    assert "請人工核對" in check.note


# ---------- 規則判斷:兩槽並列最高分 ----------


def test_rule_ties_between_two_slots_do_not_confidently_confirm_either():
    """原處分書的教示條款常寫「得提起訴願書」,會讓 appeal 與 disposition 同時命中各自的強特徵。
    並列頂格不代表任何一邊可以放心判定為真,應回報判斷不出來,不得逕自判 True 也不得逕自判 False。"""
    text = "訴願書 裁處書"  # 分別命中 appeal、disposition 的強特徵,各 3 分,無其他輔助特徵

    as_disposition = check_document("disposition", text)
    as_appeal = check_document("appeal", text)

    assert as_disposition.matched is None
    assert as_disposition.method == "none"
    assert as_appeal.matched is None
    assert as_appeal.method == "none"


def test_rule_disposition_matches_non_fine_notice_via_說明_keyword():
    """非罰鍰型不利處分(駁回申請/撤銷資格等一般公文)沒有「罰鍰」,但仍有「主旨/說明/法令依據」等公文段名。"""
    text = "主旨:駁回訴願人之申請案。\n說明:一、依相關法令規定,不予許可。\n法令依據:...\n教示:得於30日內提起訴願。"
    check = check_document("disposition", text)
    assert check.matched is True
    assert check.method == "rule"


# ---------- Gemini:prompt 與 schema ----------


def test_gemini_schema_requires_reasoning_before_document_type():
    """reasoning 先於 document_type 排列,引導模型先盤點證據再下結論,不是先射箭再畫靶。"""
    from app.document_check import _GEMINI_SCHEMA

    props = list(_GEMINI_SCHEMA["properties"].keys())
    assert props.index("reasoning") < props.index("document_type")
    assert _GEMINI_SCHEMA["required"] == ["reasoning", "document_type"]


def test_head_tail_keeps_both_ends_of_long_text():
    from app.document_check import _head_tail

    text = "頭" * 3000 + "中間可丟" * 100 + "尾" * 3000
    result = _head_tail(text)
    assert result.startswith("頭" * 2000)
    assert result.endswith("尾" * 2000)
    assert "中間可丟" not in result


def test_head_tail_returns_short_text_unchanged():
    from app.document_check import _head_tail

    assert _head_tail("短文字") == "短文字"


# ---------- 邊界 ----------


def test_empty_text_reports_none_without_calling_gemini(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key")

    class _ShouldNotBeCalled:
        def post(self, *args, **kwargs):
            raise AssertionError("空文字不應呼叫 Gemini")

    check = check_document("appeal", "   ", http_client=_ShouldNotBeCalled())

    assert check.matched is None
    assert check.method == "none"


def test_appeal_form_with_spaced_title_is_not_mistaken_for_a_disposition():
    """官方訴願書範本標題排版為「訴　　願　　書」,表內又有「處分書發文日期及字號」欄——
    比對前不去空白,強特徵落空而「處分書」命中,整份訴願書會被判成原處分書。"""
    text = (
        "訴    願    書\n訴願人 姓名 王大明\n原處分機關 新北市政府環境保護局\n"
        "處分書發文日期及字號 中華民國112 年1 月10 日新北環稽字第41-112-010273 號\n"
        "訴願請求事項：請求撤銷原處分。\n"
        "事    實\n一、原處分機關裁處罰鍰新臺幣1,200 元。\n其事實認定顯有未洽。\n"
        "收受或知悉行政處分日期：中華民國114 年9 月20 日"
    )

    check = check_document("appeal", text)

    assert check.matched is True
    assert check.method == "rule"


def test_disposition_issued_as_a_plain_official_letter_is_recognised():
    """非罰鍰型的原處分以一般公文「函」作成,沒有罰鍰與教示條款,
    僅靠「主旨」「說明」達不到門檻;「受文者」是官方發文獨有而訴願書/送達證書沒有的欄名。"""
    text = (
        "新北市政府違章建築拆除大隊 函\n受文者:何冠亮\n"
        "發文日期:中華民國114 年4 月23 日\n發文字號:新北拆認一字第1143254864 號\n"
        "主旨:有關台端陳情事項,復如說明。\n"
        "說明:一、復台端114 年4 月10 日存證信函。\n二、經現場勘查,該處為水池,非屬建築物。"
    )

    check = check_document("disposition", text)

    assert check.matched is True
    assert check.method == "rule"
