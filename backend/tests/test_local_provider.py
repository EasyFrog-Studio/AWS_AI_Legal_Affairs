"""LocalProvider 單元測試:注入 fake http_client/connect,不打真實 ollama/Postgres。"""
import json as json_mod

from app.models import CaseInfo, LawRef, ScreeningResult
from app.providers.local import LocalProvider

_DEFAULT_EMBED_VEC = [0.1] * 1024
_DEFAULT_VEC_LITERAL = "[" + ",".join(str(x) for x in _DEFAULT_EMBED_VEC) + "]"


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeHTTP:
    """記錄所有 post() 呼叫;/api/chat 依序吐出 chat_payloads,/api/embed 固定回傳假向量。"""

    def __init__(self, chat_payloads=None):
        self.calls = []
        self._chat_payloads = list(chat_payloads or [])

    def post(self, path, json=None):
        self.calls.append((path, json))
        if path == "/api/chat":
            payload = self._chat_payloads.pop(0)
            return FakeResponse({"message": {"content": json_mod.dumps(payload, ensure_ascii=False)}})
        if path == "/api/embed":
            return FakeResponse({"embeddings": [_DEFAULT_EMBED_VEC]})
        raise AssertionError(f"unexpected path: {path}")


class FakeCursor:
    def __init__(self, queue, calls):
        self._queue = queue
        self._calls = calls
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self._calls.append((sql, params))
        self._result = self._queue.pop(0) if self._queue else []

    def fetchall(self):
        return self._result


class FakeConnection:
    def __init__(self, queue, calls):
        self._queue = queue
        self._calls = calls

    def cursor(self):
        return FakeCursor(self._queue, self._calls)


def _fake_connect_factory(*result_batches):
    """回傳 (connect_callable, calls_list);result_batches 依序對應每次 execute() 後 fetchall() 的結果。"""
    queue = list(result_batches)
    calls: list = []

    def connect():
        return FakeConnection(queue, calls)

    return connect, calls


def test_db_connection_created_once_and_reused_across_queries():
    # F2 會觸發兩次查詢(law_chunks 檢索 + law_articles 精查),連線只能建立一次並重用
    inner_connect, calls = _fake_connect_factory([], [])
    count = {"n": 0}

    def counting_connect():
        count["n"] += 1
        return inner_connect()

    provider = LocalProvider(http_client=FakeHTTP(), connect=counting_connect)
    provider.recommend_laws(_info(cited_articles=["訴願法#77"]))
    assert len(calls) == 2
    assert count["n"] == 1


def _provider(http_client=None, connect=None) -> LocalProvider:
    if connect is None:
        connect, _ = _fake_connect_factory()
    return LocalProvider(
        http_client=http_client if http_client is not None else FakeHTTP(),
        connect=connect,
    )


def _info(**overrides) -> CaseInfo:
    base = dict(
        appellant="王大明",
        agency="彰化縣環境保護局",
        disposition_date="110年3月5日",
        disposition_no="彰環廢字第1號",
        disposition_summary="裁處罰鍰",
        case_type="廢棄物清理",
        issues=["是否構成任意棄置"],
        cited_articles=["廢棄物清理法#27"],
    )
    base.update(overrides)
    return CaseInfo(**base)


# ---------- a. F1 ----------
def test_extract_case_info_calls_chat_with_json_schema_and_model():
    http = FakeHTTP(
        chat_payloads=[
            {
                "appellant": "王大明",
                "agency": "彰化縣環境保護局",
                "disposition_date": "110年3月5日",
                "disposition_no": "彰環廢字第1號",
                "disposition_summary": "裁處罰鍰",
                "case_type": "廢棄物清理",
            }
        ]
    )
    provider = _provider(http_client=http)

    info = provider.extract_case_info("訴願書原文")

    assert isinstance(info, CaseInfo)
    assert info.appellant == "王大明"

    assert len(http.calls) == 1
    path, body = http.calls[0]
    assert path == "/api/chat"
    assert "model" in body
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0, "num_ctx": 16384}
    assert body["format"]["type"] == "object"
    assert "appellant" in body["format"]["properties"]


# ---------- b. screening ----------
def test_screen_admissibility_calls_chat_and_parses_result():
    http = FakeHTTP(
        chat_payloads=[
            {"passed": False, "matched_clause": "77條第2款", "reasoning": "逾期提起"},
        ]
    )
    provider = _provider(http_client=http)

    result = provider.screen_admissibility(_info(), "訴願書原文")

    assert isinstance(result, ScreeningResult)
    assert result.passed is False
    assert result.matched_clause == "77條第2款"

    path, body = http.calls[0]
    assert path == "/api/chat"
    assert body["format"]["properties"]["passed"]["type"] == "boolean"


# ---------- b2. assess_standing(§77(3)) ----------
def test_assess_standing_calls_chat_and_parses_result():
    http = FakeHTTP(
        chat_payloads=[
            {
                "referenced_norm": "廢棄物清理法#27",
                "has_standing": True,
                "reasoning": "訴願人為受處分行為之共有人,具利害關係",
            }
        ]
    )
    provider = _provider(http_client=http)

    result = provider.assess_standing(_info(), "訴願書原文")

    assert result.referenced_norm == "廢棄物清理法#27"
    assert result.has_standing is True
    path, body = http.calls[0]
    assert path == "/api/chat"
    assert body["format"]["properties"]["has_standing"]["type"] == ["boolean", "null"]
    assert body["options"]["temperature"] == 0  # §77(3)保護規範判斷須與其他呼叫一樣是決定性的


def test_assess_standing_returns_none_when_evidence_insufficient():
    http = FakeHTTP(chat_payloads=[{"referenced_norm": "", "has_standing": None, "reasoning": "證據不足"}])
    provider = _provider(http_client=http)

    result = provider.assess_standing(_info(), "訴願書原文")
    assert result.referenced_norm == ""
    assert result.has_standing is None


def test_assess_standing_is_deterministic_across_repeated_calls():
    """同一輸入連跑兩次要得到同一結果(實作計畫§Ticket 6 約束3)——這裡驗證的是呼叫形狀
    固定 temperature=0,兩次呼叫用同一份 fake payload 模擬「模型在溫度0下的穩定輸出」。"""
    payload = {"referenced_norm": "廢棄物清理法#27", "has_standing": False, "reasoning": "僅單純事實上利害關係"}
    provider = _provider(http_client=FakeHTTP(chat_payloads=[payload, dict(payload)]))

    first = provider.assess_standing(_info(), "訴願書原文")
    second = provider.assess_standing(_info(), "訴願書原文")

    assert first == second


# ---------- c. F2 ----------
def test_recommend_laws_excludes_general_law_and_does_not_call_chat():
    http = FakeHTTP(chat_payloads=[])
    law_row = (
        "廢棄物清理法#27",
        "廢棄物清理法第27條全文",
        {
            "law_name": "廢棄物清理法",
            "article_no": "27",
            "amend_date": "民國106年01月18日",
            "law_type": "實體法",
            "source_file": "markdown/相關法規/廢棄物清理法.md",
        },
        0.9,
    )
    connect, calls = _fake_connect_factory([law_row])
    provider = _provider(http_client=http, connect=connect)

    laws = provider.recommend_laws(_info())

    assert not any(path == "/api/chat" for path, _ in http.calls)
    assert any(path == "/api/embed" for path, _ in http.calls)

    sql, params = calls[0]
    assert "law_type" in sql and "普通法" in sql

    assert len(laws) == 1
    assert laws[0].amend_date == "民國106年01月18日"
    assert laws[0].law_name == "廢棄物清理法"


def test_recommend_laws_embed_query_uses_only_first_issue():
    """issues 全句串接會稀釋語意重心,查詢只該帶案由與首個爭點,不含其他爭點文字。"""
    http = FakeHTTP(chat_payloads=[])
    connect, calls = _fake_connect_factory([])
    provider = _provider(http_client=http, connect=connect)

    provider.recommend_laws(_info(issues=["是否構成任意棄置", "是否符合行政罰法第7條之故意過失"]))

    embed_calls = [body for path, body in http.calls if path == "/api/embed"]
    assert len(embed_calls) == 1
    assert embed_calls[0]["input"] == ["廢棄物清理 是否構成任意棄置"]


def test_recommend_laws_missing_cited_article_falls_back_to_未收錄():
    http = FakeHTTP(chat_payloads=[])
    # 第一批:law_chunks 向量檢索(空);第二批:law_articles 精查(空)
    connect, calls = _fake_connect_factory([], [])
    provider = _provider(http_client=http, connect=connect)

    laws = provider.recommend_laws(_info(cited_articles=["廢棄物清理法#99"]))

    assert len(laws) == 1
    assert laws[0].amend_date == "未收錄"
    assert laws[0].law_name == "廢棄物清理法"
    assert laws[0].article_no == "99"
    assert len(calls) == 2  # law_chunks 檢索 + law_articles 精查


def test_recommend_laws_skips_placeholder_cited_article_without_querying_law_articles():
    """F1 對抽不到條號的欄位填「未載明」,這種假條號不該送進 law_articles 精查,也不該出現在結果裡。"""
    http = FakeHTTP(chat_payloads=[])
    connect, calls = _fake_connect_factory([])  # 只有 law_chunks 向量檢索這一批
    provider = _provider(http_client=http, connect=connect)

    laws = provider.recommend_laws(_info(cited_articles=["廢棄物清理法#未載明"]))

    assert laws == []
    assert len(calls) == 1  # 只查了 law_chunks,沒有觸發 law_articles 精查


def test_recommend_laws_cited_article_found_in_law_articles_table():
    http = FakeHTTP(chat_payloads=[])
    law_articles_row = (
        "某法#5",
        "某法第5條全文",
        {"law_name": "某法", "article_no": "5", "amend_date": "民國100年01月01日"},
    )
    connect, calls = _fake_connect_factory([], [law_articles_row])
    provider = _provider(http_client=http, connect=connect)

    laws = provider.recommend_laws(_info(cited_articles=["某法#5"]))

    assert len(laws) == 1
    assert laws[0].amend_date == "民國100年01月01日"
    assert laws[0].law_name == "某法"
    assert laws[0].article_no == "5"

    second_sql, second_params = calls[1]
    assert "law_articles" in second_sql
    assert second_params == (["某法#5"],)


# ---------- d. F3 ----------
def test_find_similar_cases_admissible_filter_uses_case_type_only():
    http = FakeHTTP(chat_payloads=[])
    connect, calls = _fake_connect_factory([], [], [])
    provider = _provider(http_client=http, connect=connect)

    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過審查")
    provider.find_similar_cases(_info(), screening, "原文")

    # 三層 fallback:case_type → case_type(放寬)→ 純語意(無 filter)
    assert len(calls) == 3
    first_sql, first_params = calls[0]
    assert "case_type" in first_sql
    assert "result" not in first_sql
    last_sql, last_params = calls[-1]
    assert "WHERE" not in last_sql
    # chunk 取用量要大於呈現筆數,否則同一案號的多個段落會把三件不同案例佔滿
    assert last_params[-1] > 3


def test_find_similar_cases_inadmissible_filter_includes_result_and_appeal_article():
    http = FakeHTTP(chat_payloads=[])
    row = (
        "case1",
        "相似案例全文",
        {
            "case_no": "北市訴字第1號",
            "year": "109",
            "case_type": "社會救助",
            "appeal_article": "77(2)",
            "issue": "逾期提起",
            "result": "不受理",
            "source_file": "markdown/歷史訴願決定書/北市訴字第1號.md",
        },
        0.8,
    )
    connect, calls = _fake_connect_factory([row])
    provider = _provider(http_client=http, connect=connect)
    screening = ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期")
    info = _info(case_type="社會救助")

    cases = provider.find_similar_cases(info, screening, "原文")

    sql, params = calls[0]
    assert "case_type" in sql
    assert "result" in sql
    assert "appeal_article" in sql
    assert params[0] == _DEFAULT_VEC_LITERAL
    assert params[1:4] == ("社會救助", "不受理", "77(2)")
    assert params[-2] == _DEFAULT_VEC_LITERAL
    # chunk 取用量要大於呈現筆數,否則同一案號的多個段落會把三件不同案例佔滿
    assert params[-1] > 3

    assert len(cases) == 1
    assert cases[0].case_no == "北市訴字第1號"


def test_find_similar_cases_retries_with_relaxed_filter_when_empty():
    http = FakeHTTP(chat_payloads=[])
    row2 = (
        "case2",
        "相似案例全文",
        {
            "case_no": "彰府訴字第1號",
            "year": "109",
            "case_type": "廢棄物清理",
            "appeal_article": "無",
            "issue": "任意棄置",
            "result": "駁回",
            "source_file": "x.md",
        },
        0.7,
    )
    connect, calls = _fake_connect_factory([], [row2])
    provider = _provider(http_client=http, connect=connect)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")

    cases = provider.find_similar_cases(_info(), screening, "原文")

    assert len(calls) == 2
    second_sql, second_params = calls[1]
    assert "case_type" in second_sql
    assert "result" not in second_sql
    assert len(cases) == 1


# ---------- e. F4 ----------
def test_generate_draft_calls_chat_and_strips_disallowed_laws():
    http = FakeHTTP(
        chat_payloads=[
            {
                "draft_type": "駁回",
                "fact": "事實",
                "reason": "理由",
                "main_text": "訴願駁回。",
                "cited_laws": ["廢棄物清理法#27", "自創法#999"],
            }
        ]
    )
    provider = _provider(http_client=http)
    laws = [
        LawRef(
            law_name="廢棄物清理法",
            article_no="27",
            text="條文",
            amend_date="民國106年01月18日",
            source_key=None,
            relevance="相關",
        )
    ]
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")

    draft = provider.generate_draft(_info(), screening, laws, [])

    assert draft.cited_laws == ["廢棄物清理法#27"]
    assert "自創法#999" not in draft.cited_laws

    path, body = http.calls[0]
    assert path == "/api/chat"
    assert body["format"]["properties"]["draft_type"]["enum"] == ["不受理", "駁回", "原處分撤銷"]


# ---------- f. 建構子注入 fake 完全不觸碰 settings 新欄位與 psycopg ----------
def test_constructor_with_injected_fakes_never_imports_psycopg():
    import sys

    assert "psycopg" not in sys.modules
    provider = _provider()
    assert isinstance(provider, LocalProvider)
    assert "psycopg" not in sys.modules


# ---------- get_law_articles:條號精查 ----------


def test_get_law_articles_returns_ref_from_law_articles_table():
    connect, calls = _fake_connect_factory(
        [
            (
                "訴願法#77",
                "訴願事件有左列各款情形之一者,應為不受理之決定:…",
                {
                    "law_name": "訴願法",
                    "article_no": "77",
                    "amend_date": "民國101年06月27日",
                    "source_key": "laws/訴願法.md",
                },
            )
        ]
    )
    provider = _provider(connect=connect)

    refs = provider.get_law_articles(["訴願法#77"])

    assert len(refs) == 1
    assert refs[0].law_name == "訴願法"
    assert refs[0].article_no == "77"
    assert refs[0].amend_date == "民國101年06月27日"  # 來自 law_articles,非 LLM 生成
    sql, params = calls[0]
    assert "law_articles" in sql and params == (["訴願法#77"],)


def test_get_law_articles_missing_key_falls_back_to_placeholder():
    connect, _ = _fake_connect_factory([])
    provider = _provider(connect=connect)

    refs = provider.get_law_articles(["訴願法#77"])

    assert len(refs) == 1
    assert refs[0].law_name == "訴願法"
    assert refs[0].text == ""
    assert refs[0].amend_date == "未收錄"  # 查無資料填死值,不得由 LLM 生成


def test_get_law_articles_empty_keys_skips_query():
    connect, calls = _fake_connect_factory()
    provider = _provider(connect=connect)

    assert provider.get_law_articles([]) == []
    assert calls == []


def test_f2_retrieval_is_restricted_to_statutes():
    """函釋/釋字/裁判已入庫但不供 F2 推薦:它們沒有條號,F2 的 LawRef 與 law_articles
    精查都以「法名#條號」為鍵,混進來只會產出認不出來源的空殼列。"""
    http = FakeHTTP(chat_payloads=[])
    connect, calls = _fake_connect_factory([])
    provider = _provider(http_client=http, connect=connect)

    provider.recommend_laws(_info())

    sql, params = calls[0]
    assert "doc_kind" in sql and "法規" in sql
    assert "law_type" in sql and "普通法" in sql  # 既有的排除普通法不能因此掉了

# ---------- 推薦筆數上限 ----------
def _case_row(case_no, section="事實"):
    return (
        f"{case_no}#{section}",
        f"【{section}】{case_no} 本件訴願人不服原處分…",
        {
            "case_no": case_no,
            "year": "112",
            "case_type": "廢棄物清理",
            "appeal_article": "",
            "issue": "任意棄置",
            "result": "駁回",
            "source_file": f"{case_no}.pdf",
        },
        0.9,
    )


def test_recommend_laws_asks_db_for_three_chunks():
    connect, calls = _fake_connect_factory([])
    provider = _provider(connect=connect)

    provider.recommend_laws(_info(cited_articles=[]))

    _sql, params = calls[0]
    assert params[-1] == 3


def test_find_similar_cases_returns_at_most_three_distinct_cases():
    """一份決定書切成多個 chunk,靠 SQL LIMIT 湊不出三件不同案號,須以案號去重後截斷。"""
    rows = [
        _case_row("112-0001", "事實"),
        _case_row("112-0001", "理由"),
        _case_row("112-0002"),
        _case_row("112-0003"),
        _case_row("112-0004"),
    ]
    connect, _ = _fake_connect_factory(rows)
    provider = _provider(connect=connect)

    cases = provider.find_similar_cases(
        _info(), ScreeningResult(passed=True, matched_clause=None, reasoning="通過"), "原文"
    )

    assert [c.case_no for c in cases] == ["112-0001", "112-0002", "112-0003"]
