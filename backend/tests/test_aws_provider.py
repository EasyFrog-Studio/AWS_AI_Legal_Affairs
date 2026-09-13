"""AWSProvider 單元測試:mock boto3 client,驗證參數組裝,不做真實呼叫。"""
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.models import AGENT_ROLES, SERVICE_METHODS, CaseInfo, ScreeningResult, LawRef
from app.providers.aws import AWSProvider, _clause_to_appeal_article, _retrieval_query, _valid_cited_articles
from app.providers.schemas import case_info_json_schema


def _provider(**clients) -> AWSProvider:
    return AWSProvider(
        bedrock_runtime=clients.get("bedrock_runtime", MagicMock()),
        bedrock_agent_runtime=clients.get("bedrock_agent_runtime", MagicMock()),
        dynamodb_resource=clients.get("dynamodb_resource", MagicMock()),
    )


def _filtering_retrieve(rows: list[dict]):
    """假 KB:真的套用 retrieve 帶進來的 metadata filter。回 MagicMock 固定清單問不出
    「filter 有沒有擋掉東西」——那種假 client 在 filter 被改壞時照樣綠。"""

    def _matches(cond: dict, meta: dict) -> bool:
        if "andAll" in cond:
            return all(_matches(c, meta) for c in cond["andAll"])
        if "equals" in cond:
            return meta.get(cond["equals"]["key"]) == cond["equals"]["value"]
        if "notEquals" in cond:
            return meta.get(cond["notEquals"]["key"]) != cond["notEquals"]["value"]
        raise AssertionError(f"假 KB 未支援的 filter 形狀: {cond}")

    def _retrieve(**kwargs):
        config = kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]
        cond = config.get("filter")
        kept = rows if cond is None else [r for r in rows if _matches(cond, r["metadata"])]
        return {"retrievalResults": kept[: config["numberOfResults"]]}

    return _retrieve


class _LawIdDdb:
    """假 DynamoDB:law_id 是主鍵,與實際 appeal_law_articles 一致。
    `unprocessed` 模擬被限流而沒查成的鍵(僅套用在 law_id 精查路徑),那與查無是兩回事。"""

    def __init__(self, items_by_law_id: dict, unprocessed: bool = False):
        self._items = items_by_law_id
        self._unprocessed = unprocessed
        self.batch_get_calls: list = []

    def batch_get_item(self, RequestItems):
        self.batch_get_calls.append(RequestItems)
        table = next(iter(RequestItems))
        keys = RequestItems[table]["Keys"]
        if keys and "law_id" in keys[0]:
            ids = [key["law_id"] for key in keys]
            resp = {"Responses": {table: [self._items[i] for i in ids if i in self._items]}}
            if self._unprocessed:
                resp["UnprocessedKeys"] = {table: {"Keys": keys}}
            return resp
        articles = [key["law_article"] for key in keys]
        return {"Responses": {table: [i for i in self._items.values() if i["law_article"] in articles]}}


@pytest.fixture
def _date_field_revised():
    before = settings.DDB_LAW_DATE_FIELD
    settings.DDB_LAW_DATE_FIELD = "revised_date"
    yield
    settings.DDB_LAW_DATE_FIELD = before


def _ddb_law(law_id: int, law_name: str, article_no: str, revised: str, law_type: str = "實體法") -> dict:
    return {
        "law_id": law_id,
        "law_article": f"{law_name}#{article_no}",
        "law_name": law_name,
        "article_no": article_no,
        "text": f"{law_name}第{article_no}條全文",
        "revised_date": revised,
        "law_type": law_type,
        "source_url": f"https://law.moj.gov.tw/LawClass/LawSingle.aspx?flno={article_no}",
    }


def _toolUse_response(tool_name: str, input_data: dict) -> dict:
    return {
        "output": {
            "message": {
                "content": [{"toolUse": {"name": tool_name, "input": input_data}}]
            }
        }
    }


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


def test_clause_to_appeal_article_conversion():
    assert _clause_to_appeal_article("77條第2款") == "77(2)"
    assert _clause_to_appeal_article(None) is None
    assert _clause_to_appeal_article("不合法定格式") is None


# ---------- _valid_cited_articles:過濾 F1 產出的假條號 ----------


def test_valid_cited_articles_keeps_numeric_article_no():
    assert _valid_cited_articles(["廢棄物清理法#27", "行政罰法#44-1"]) == [
        "廢棄物清理法#27",
        "行政罰法#44-1",
    ]


def test_valid_cited_articles_drops_placeholder_未載明():
    """F1 找不到明確依據時依 prompt 要求填「未載明」,這種鍵送進精查必然查無,不該進可引用清單。"""
    assert _valid_cited_articles(["廢棄物清理法#未載明"]) == []


def test_valid_cited_articles_drops_empty_and_non_numeric():
    assert _valid_cited_articles(["廢棄物清理法#", "廢棄物清理法#abc", "廢棄物清理法#27"]) == [
        "廢棄物清理法#27"
    ]


# ---------- _retrieval_query:F2/F3 共用的查詢字串組裝 ----------


def test_retrieval_query_uses_only_first_issue_not_all_joined():
    """issues 全句串接會把語意重心稀釋,只取首個爭點(截斷)才是決定該撈哪部法規的關鍵訊號。"""
    info = _info(case_type="廢棄物清理", issues=["是否構成任意棄置", "是否符合行政罰法第7條之故意過失"])
    query = _retrieval_query(info)
    assert query == "廢棄物清理 是否構成任意棄置"
    assert "行政罰法" not in query


def test_retrieval_query_truncates_long_issue():
    long_issue = "是" * 100
    info = _info(issues=[long_issue])
    query = _retrieval_query(info)
    assert len(query) <= len("廢棄物清理 ") + 40


def test_retrieval_query_handles_no_issues():
    info = _info(issues=[])
    assert _retrieval_query(info) == "廢棄物清理"


def test_extract_case_info_uses_converse_toolConfig_json_schema():
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response(
        "extract_case_info",
        {
            "appellant": "王大明",
            "agency": "彰化縣環境保護局",
            "disposition_date": "110年3月5日",
            "disposition_no": "彰環廢字第1號",
            "disposition_summary": "裁處罰鍰",
            "case_type": "廢棄物清理",
        },
    )
    provider = _provider(bedrock_runtime=brt)
    info = provider.extract_case_info("訴願書原文")

    assert isinstance(info, CaseInfo)
    assert info.appellant == "王大明"

    _, kwargs = brt.converse.call_args
    assert kwargs["toolConfig"]["toolChoice"] == {"tool": {"name": "extract_case_info"}}
    schema = kwargs["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert schema["type"] == "object"
    assert "appellant" in schema["properties"]


def test_assess_standing_uses_converse_toolConfig_json_schema():
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response(
        "assess_standing",
        {
            "referenced_norm": "廢棄物清理法#27",
            "has_standing": True,
            "reasoning": "訴願人為受處分行為之共有人,具利害關係",
        },
    )
    provider = _provider(bedrock_runtime=brt)

    result = provider.assess_standing(_info(), "訴願書原文")

    assert result.referenced_norm == "廢棄物清理法#27"
    assert result.has_standing is True
    _, kwargs = brt.converse.call_args
    assert kwargs["toolConfig"]["toolChoice"] == {"tool": {"name": "assess_standing"}}
    assert kwargs["inferenceConfig"]["temperature"] == 0  # 全流程唯一的價值判斷節點,須是決定性的


def test_assess_standing_returns_false_when_no_standing():
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response(
        "assess_standing",
        {"referenced_norm": "廢棄物清理法#27", "has_standing": False, "reasoning": "僅單純事實上利害關係"},
    )
    provider = _provider(bedrock_runtime=brt)

    result = provider.assess_standing(_info(), "訴願書原文")
    assert result.has_standing is False


def test_assess_standing_returns_none_when_evidence_insufficient():
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response(
        "assess_standing", {"referenced_norm": "", "has_standing": None, "reasoning": "證據不足,無法判斷"}
    )
    provider = _provider(bedrock_runtime=brt)

    result = provider.assess_standing(_info(), "訴願書原文")
    assert result.referenced_norm == ""
    assert result.has_standing is None


def test_assess_standing_is_deterministic_across_repeated_calls():
    """同一輸入連跑兩次要得到同一結果:這裡驗證呼叫形狀本身是
    決定性的(固定 temperature=0),用同一份回應模擬模型在溫度0下的穩定輸出。"""
    payload = {"referenced_norm": "廢棄物清理法#27", "has_standing": False, "reasoning": "僅單純事實上利害關係"}
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response("assess_standing", payload)
    provider = _provider(bedrock_runtime=brt)

    first = provider.assess_standing(_info(), "訴願書原文")
    second = provider.assess_standing(_info(), "訴願書原文")

    assert first == second
    for call in brt.converse.call_args_list:
        assert call.kwargs["inferenceConfig"]["temperature"] == 0


def test_standing_prompt_embeds_article_18_and_protective_norm_theory():
    from app.providers.aws import _load_prompt

    text = _load_prompt("standing.txt")
    assert "第18條" in text
    assert "保護規範理論" in text
    assert "referenced_norm" in text


def test_standing_prompt_embeds_verbatim_interpretation_469():
    """釋字469的保護規範判準要 verbatim 注入,不是程式產生的摘要。"""
    from app.providers.aws import _load_prompt

    text = _load_prompt("standing.txt")
    assert "如法律明確規定特定人得享有權利" in text
    assert "第469號" in text


def test_screening_prompt_embeds_full_article_77_text():
    from app.providers.aws import _load_prompt

    text = _load_prompt("screening.txt")
    assert "行政處分已不存在者" in text
    assert "對於非行政處分或其他依法不屬訴願救濟範圍內之事項提起訴願者" in text


# ---------- F2:法規推薦(候選來自 F3 案例的 law_id,向量檢索 + Sonnet 重排取 3) ----------

_WASTE_LAW_IDS = {"27": 5137, "50": 5160, "12": 5122}


def _kb_law_result(article_no="27"):
    """KB-LAW 的 metadata 只帶 law_id,法規名稱與條號要由 DynamoDB 補。"""
    return {
        "content": {"text": f"廢棄物清理法第{article_no}條全文"},
        "metadata": {"law_id": str(_WASTE_LAW_IDS[article_no])},
    }


def _waste_law_ddb(*article_nos, extra: dict | None = None) -> "_LawIdDdb":
    items = {
        _WASTE_LAW_IDS[a]: _ddb_law(_WASTE_LAW_IDS[a], "廢棄物清理法", a, "民國106年01月18日")
        for a in article_nos
    }
    items.update(extra or {})
    return _LawIdDdb(items)


def _rerank_response(ranked_ids: list[str]) -> dict:
    return _toolUse_response("rerank", {"ranked_ids": ranked_ids})


def test_recommend_laws_sends_no_filter_without_candidates(_date_field_revised):
    """F3 案例全無 law_ids(或 F3 零命中)時退回全庫檢索,不送 in filter,relevance 要標出來。"""
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": [_kb_law_result("27")]}
    ddb = _waste_law_ddb("27")
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5137"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info(cited_articles=[]), None)

    _, kwargs = bart.retrieve.call_args
    config = kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]
    assert "filter" not in config
    assert config["numberOfResults"] == 25
    assert laws[0].relevance.startswith("（無案例法規可依，改採全庫檢索）")


def test_recommend_laws_sends_in_filter_with_string_law_ids_when_candidates_given(_date_field_revised):
    """候選 law_id 來自 F3 案例;KB metadata 的 law_id 是字串,in filter 值須轉字串。"""
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": [_kb_law_result("27"), _kb_law_result("50")]}
    ddb = _waste_law_ddb("27", "50")
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5137", "5160"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info(cited_articles=[]), [5137, 5160])

    _, kwargs = bart.retrieve.call_args
    filter_ = kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
    assert filter_ == {"in": {"key": "law_id", "value": ["5137", "5160"]}}
    assert not laws[0].relevance.startswith("（無案例法規可依")


def test_recommend_laws_caps_candidate_ids_at_fifty(_date_field_revised):
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": []}
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=MagicMock())

    provider.recommend_laws(_info(cited_articles=[]), list(range(1, 101)))

    _, kwargs = bart.retrieve.call_args
    filter_ = kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
    assert len(filter_["in"]["value"]) == 50
    assert filter_["in"]["value"] == [str(i) for i in range(1, 51)]


def test_recommend_laws_reranks_and_returns_top_three(_date_field_revised):
    """候選超過 3 筆時只呈現重排後的前 3 筆。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [_kb_law_result("27"), _kb_law_result("50"), _kb_law_result("12")]
    }
    ddb = _waste_law_ddb("27", "50", "12")
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5122", "5137", "5160"])  # 重排後:12,27,50

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info(cited_articles=[]), [5137, 5160, 5122])

    assert [law.article_no for law in laws] == ["12", "27", "50"]
    assert len(laws) == 3


def test_recommend_laws_rerank_called_once_with_at_most_25_candidates(_date_field_revised):
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": [_kb_law_result("27")]}
    ddb = _waste_law_ddb("27")
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5137"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    provider.recommend_laws(_info(cited_articles=[]), None)

    assert brt.converse.call_count == 1
    _, kwargs = bart.retrieve.call_args
    assert kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["numberOfResults"] == 25


def test_recommend_laws_rerank_output_out_of_order_and_with_unknown_id_is_corrected(_date_field_revised):
    """重排回傳亂序、且夾帶不在候選中的 id 時,程式驗證要修正:不在候選內的丟掉,
    漏掉的候選依原向量順序補在後面。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [_kb_law_result("27"), _kb_law_result("50"), _kb_law_result("12")]
    }
    ddb = _waste_law_ddb("27", "50", "12")
    brt = MagicMock()
    # "99999" 不在候選內;"5160"(50) 漏掉未回傳
    brt.converse.return_value = _rerank_response(["99999", "5122"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info(cited_articles=[]), [5137, 5160, 5122])

    # 5122(12) 排第一,漏掉的 5137(27)、5160(50) 依原向量順序補在後面
    assert [law.article_no for law in laws] == ["12", "27", "50"]


def test_recommend_laws_rerank_returns_nothing_usable_falls_back_to_vector_order(_date_field_revised):
    """重排回傳格式不合(全不在候選內)時,沿用向量檢索順序,不得整批掛掉。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [_kb_law_result("27"), _kb_law_result("50")]
    }
    ddb = _waste_law_ddb("27", "50")
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["不存在的id"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info(cited_articles=[]), [5137, 5160])

    assert [law.article_no for law in laws] == ["27", "50"]


def test_recommend_laws_rerank_llm_exception_propagates(_date_field_revised):
    """LLM 呼叫本身拋例外要往上拋,不得吞掉——案件因此落 error 好過安靜地用未排序的結果。"""
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": [_kb_law_result("27")]}
    ddb = _waste_law_ddb("27")
    brt = MagicMock()
    brt.converse.side_effect = RuntimeError("Bedrock converse 逾時")

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    with pytest.raises(RuntimeError, match="逾時"):
        provider.recommend_laws(_info(cited_articles=[]), None)


def test_recommend_laws_source_url_comes_from_dynamodb_item(_date_field_revised):
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": [_kb_law_result("27")]}
    item = _ddb_law(5137, "廢棄物清理法", "27", "民國106年01月18日")
    item["source_url"] = "https://law.moj.gov.tw/LawClass/LawSingle.aspx?flno=27"
    ddb = _LawIdDdb({5137: item})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5137"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    (law,) = provider.recommend_laws(_info(cited_articles=[]), [5137])

    assert law.source_url == "https://law.moj.gov.tw/LawClass/LawSingle.aspx?flno=27"
    assert law.source_key is None


def test_recommend_laws_source_url_empty_string_falls_back_to_computed_url(_date_field_revised):
    """DynamoDB item 的 source_url 存空字串(等同未填)時不得原樣頂著一個空字串,
    應視同 None 交給 model_validator 用 law_article_url 補算。"""
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": [_kb_law_result("27")]}
    item = _ddb_law(5137, "廢棄物清理法", "27", "民國106年01月18日")
    item["source_url"] = ""
    ddb = _LawIdDdb({5137: item})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5137"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    (law,) = provider.recommend_laws(_info(cited_articles=[]), [5137])

    assert law.source_url is not None
    assert law.source_url.endswith("&flno=27")


def test_recommend_laws_raises_when_no_law_id_resolves(_date_field_revised):
    """全數查無要炸開:回空清單與「本案查無相關法條」同形,承辦人分不出是資料斷了。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [
            {"content": {"text": "全文"}, "metadata": {"law_id": "99998"}},
            {"content": {"text": "全文"}, "metadata": {"law_id": "99999"}},
        ]
    }
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=_LawIdDdb({}))

    with pytest.raises(RuntimeError, match="DynamoDB"):
        provider.recommend_laws(_info(cited_articles=[]), None)


def test_recommend_laws_drops_law_id_missing_from_dynamodb(_date_field_revised):
    """查無的 law_id 沒有法名與條號,不能進候選——會以 "#" 混進 F4 的可引用法規清單。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [
            _kb_law_result("27"),
            {"content": {"text": "已從 DynamoDB 刪掉的條文"}, "metadata": {"law_id": "99999"}},
        ]
    }
    ddb = _waste_law_ddb("27")
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5137"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info(cited_articles=[]), None)

    assert [(law.law_name, law.article_no) for law in laws] == [("廢棄物清理法", "27")]
    assert "#" not in [f"{law.law_name}#{law.article_no}" for law in laws]


def test_recommend_laws_tolerates_unusable_law_id(_date_field_revised):
    """law_id 缺漏或非數字時只失去那一筆,不連累整批檢索結果。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [
            {"content": {"text": "沒有 law_id"}, "metadata": {}},
            {"content": {"text": "law_id 不是數字"}, "metadata": {"law_id": "LAW#7"}},
            _kb_law_result("27"),
        ]
    }
    ddb = _waste_law_ddb("27")
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5137"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info(cited_articles=[]), None)

    assert [(law.law_name, law.article_no) for law in laws] == [("廢棄物清理法", "27")]


def test_recommend_laws_looks_up_dynamodb_once_by_law_id(_date_field_revised):
    """一次 batch_get 查完:逐筆查會讓每則推薦各付一次往返。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [_kb_law_result("27"), _kb_law_result("50")]
    }
    ddb = _waste_law_ddb("27", "50")
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5137", "5160"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    provider.recommend_laws(_info(cited_articles=[]), None)

    assert len(ddb.batch_get_calls) == 1
    sent = ddb.batch_get_calls[0]["appeal_law_articles"]["Keys"]
    assert sent == [{"law_id": 5137}, {"law_id": 5160}]


def test_recommend_laws_raises_when_dynamodb_leaves_law_ids_unprocessed(_date_field_revised):
    """被限流而沒查成的 law_id 不得當成查無:那會讓資料層被限流長得像「這條法規沒收錄」。"""
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": [_kb_law_result("27")]}
    ddb = _LawIdDdb(
        {5137: _ddb_law(5137, "廢棄物清理法", "27", "民國106年01月18日")}, unprocessed=True
    )

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb)
    with pytest.raises(RuntimeError, match="未處理"):
        provider.recommend_laws(_info(cited_articles=[]), None)


def test_recommend_laws_dedupes_law_ids_sharing_one_article(_date_field_revised):
    """同一條法規切成多個 chunk 時各自帶同一個 law_id;重複鍵會讓整批 BatchGetItem 被退回。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [_kb_law_result("27"), _kb_law_result("27")]
    }
    ddb = _waste_law_ddb("27")
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5137"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info(cited_articles=[]), None)

    sent = ddb.batch_get_calls[0]["appeal_law_articles"]["Keys"]
    assert sent == [{"law_id": 5137}]
    assert [(law.law_name, law.article_no) for law in laws] == [("廢棄物清理法", "27")]


def test_recommend_laws_keeps_general_law_recommendations(_date_field_revised):
    """民法是 §77(4) 無訴願能力案的法源(訴願法§20 III 指向民法),不得排除。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [
            {"content": {"text": "民法第12條全文"}, "metadata": {"law_id": "7456"}},
            {"content": {"text": "訴願法第20條全文"}, "metadata": {"law_id": "11269"}},
        ]
    }
    ddb = _LawIdDdb({
        7456: _ddb_law(7456, "民法", "12", "民國110年01月20日", law_type="普通法"),
        11269: _ddb_law(11269, "訴願法", "20", "民國101年06月27日", law_type="程序法"),
    })
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["7456", "11269"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info(cited_articles=[]), None)

    assert ("民法", "12") in [(law.law_name, law.article_no) for law in laws]


# ---------- F2:F1 cited_articles 精查附加(AWS_PLAN.md「精查 + 語意兩條路合併去重」) ----------


def test_recommend_laws_appends_a_cited_article_not_in_the_top_three(_date_field_revised):
    """F1 從原處分書／訴願書抽到的引用條號是 100% 準確來源,附加在重排 top 3 之後,
    不佔用重排名額;relevance 要標明來源,F4 的 cited_laws 後置過濾才引得到它。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [_kb_law_result("27"), _kb_law_result("50"), _kb_law_result("12")]
    }
    ddb = _waste_law_ddb(
        "27", "50", "12",
        extra={6000: _ddb_law(6000, "廢棄物清理法", "99", "民國100年05月01日")},
    )
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5137", "5160", "5122"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info(cited_articles=["廢棄物清理法#99"]), [5137, 5160, 5122])

    assert len(laws) == 4
    assert (laws[3].law_name, laws[3].article_no) == ("廢棄物清理法", "99")
    assert laws[3].relevance == "原處分書／訴願書明文引用"


def test_recommend_laws_does_not_duplicate_a_cited_article_already_in_the_top_three(_date_field_revised):
    """cited 條號與重排 top 3 重複時不得重複列出,長度仍為 3。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [_kb_law_result("27"), _kb_law_result("50"), _kb_law_result("12")]
    }
    ddb = _waste_law_ddb("27", "50", "12")
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5137", "5160", "5122"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info(cited_articles=["廢棄物清理法#27"]), [5137, 5160, 5122])

    assert len(laws) == 3
    assert [f"{l.law_name}#{l.article_no}" for l in laws].count("廢棄物清理法#27") == 1


def test_recommend_laws_ignores_a_cited_article_dynamodb_cannot_resolve(_date_field_revised):
    """DynamoDB 精查不到 cited 條號就忽略,不報錯、不生空殼條目。"""
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": [_kb_law_result("27")]}
    ddb = _waste_law_ddb("27")
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["5137"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info(cited_articles=["查無此法#1"]), [5137])

    assert len(laws) == 1
    assert [f"{l.law_name}#{l.article_no}" for l in laws] == ["廢棄物清理法#27"]


# ---------- F3:相似案例(appeal_facts/appeal_reasons 兩路查詢 RRF 合流,重排 25 件) ----------


class _PastDecisionDdb:
    """假 DynamoDB:appeal_past_decisions 以 case_id 為主鍵。
    `unprocessed` 模擬被限流而沒查成的鍵,那與查無是兩回事。"""

    def __init__(self, items_by_case_id: dict, unprocessed: bool = False):
        self._items = items_by_case_id
        self._unprocessed = unprocessed
        self.batch_get_calls: list = []

    def batch_get_item(self, RequestItems):
        self.batch_get_calls.append(RequestItems)
        table = next(iter(RequestItems))
        keys = RequestItems[table]["Keys"]
        ids = [key["case_id"] for key in keys]
        resp = {"Responses": {table: [self._items[i] for i in ids if i in self._items]}}
        if self._unprocessed:
            resp["UnprocessedKeys"] = {table: {"Keys": keys}}
        return resp


def _ddb_case(case_id: str, **overrides) -> dict:
    item = {
        "case_id": case_id,
        "case_no": case_id.removeprefix("NTPC-"),
        "year": "112",
        "case_type": "廢棄物清理法",
        "case_subtype": "廢棄物清理法",
        "appeal_article": "",
        "issue": "",
        "result": "駁回",
        "source_url": f"https://web.law.ntpc.gov.tw/Scripts/Su_contents03.aspx?EANO={case_id}",
        "source_file": case_id,
    }
    item.update(overrides)
    return item


def _case_chunk(case_id: str, text: str = "案情內容", **metadata) -> dict:
    meta = {"case_id": case_id, "case_type": "廢棄物清理", "result": "駁回"}
    meta.update(metadata)
    return {"content": {"text": text}, "metadata": meta}


def _info_with_sections(**overrides) -> CaseInfo:
    base = dict(appeal_facts=["訴願人於某日遭裁處罰鍰"], appeal_reasons=["原處分認定事實有誤"])
    base.update(overrides)
    return _info(**base)


def test_find_similar_cases_queries_appeal_facts_and_appeal_reasons_separately():
    """兩路查詢:一路 appeal_facts、一路 appeal_reasons,同一 filter,各撈 100 chunk。
    tier1 兩路皆非空,不觸發降級,故恰好各打一次。"""
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": [_case_chunk("NTPC-A")]}
    ddb = _PastDecisionDdb({"NTPC-A": _ddb_case("NTPC-A")})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["NTPC-A"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")
    info = _info_with_sections()

    provider.find_similar_cases(info, screening, "原文")

    calls = bart.retrieve.call_args_list
    assert len(calls) == 2
    queries = [c.kwargs["retrievalQuery"]["text"] for c in calls]
    assert queries[0] == "訴願人於某日遭裁處罰鍰"
    assert queries[1] == "原處分認定事實有誤"
    for c in calls:
        config = c.kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]
        assert config["numberOfResults"] == 100
        assert config["filter"] == {"equals": {"key": "case_type", "value": "廢棄物清理"}}


def test_find_similar_cases_falls_back_to_retrieval_query_when_a_section_is_empty():
    """appeal_facts 或 appeal_reasons 任一路為空字串時,該路退回 _retrieval_query(info)。"""
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": [_case_chunk("NTPC-A")]}
    ddb = _PastDecisionDdb({"NTPC-A": _ddb_case("NTPC-A")})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["NTPC-A"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")
    info = _info_with_sections(appeal_facts=[], appeal_reasons=[])

    provider.find_similar_cases(info, screening, "原文")

    queries = [c.kwargs["retrievalQuery"]["text"] for c in bart.retrieve.call_args_list]
    assert queries == ["廢棄物清理 是否構成任意棄置", "廢棄物清理 是否構成任意棄置"]


def test_find_similar_cases_inadmissible_filter_includes_result_and_appeal_article():
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [
            _case_chunk(
                "NTPC-1090011520", case_type="社會救助", appeal_article="77(2)", result="不受理", year="109"
            )
        ]
    }
    ddb = _PastDecisionDdb(
        {
            "NTPC-1090011520": _ddb_case(
                "NTPC-1090011520",
                case_no="北市訴字第1號",
                year="109",
                case_type="社會救助",
                appeal_article="77(2)",
                issue="逾期提起",
                result="不受理",
            )
        }
    )
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["NTPC-1090011520"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    screening = ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期")
    info = _info_with_sections(case_type="社會救助")

    cases = provider.find_similar_cases(info, screening, "原文")

    filter_ = bart.retrieve.call_args_list[0].kwargs["retrievalConfiguration"]["vectorSearchConfiguration"][
        "filter"
    ]
    assert filter_ == {
        "andAll": [
            {"equals": {"key": "case_type", "value": "社會救助"}},
            {"equals": {"key": "result", "value": "不受理"}},
            {"equals": {"key": "appeal_article", "value": "77(2)"}},
        ]
    }
    assert len(cases) == 1
    assert cases[0].case_no == "北市訴字第1號"


def test_find_similar_cases_falls_back_through_filter_tiers_when_both_queries_are_empty():
    """兩路查詢在某一層 filter 都是 0 筆才降級;受理案第一、二層皆為 case_type,第三層去 filter。"""
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": []},  # tier1 facts
        {"retrievalResults": []},  # tier1 reasons
        {"retrievalResults": []},  # tier2 facts(受理案 tier2==tier1)
        {"retrievalResults": []},  # tier2 reasons
        {
            "retrievalResults": [
                _case_chunk("NTPC-1090011564", case_type="廢棄物清理", result="駁回", year="109")
            ]
        },  # tier3 facts(無 filter)
        {"retrievalResults": []},  # tier3 reasons
    ]
    ddb = _PastDecisionDdb({"NTPC-1090011564": _ddb_case("NTPC-1090011564", case_no="彰府訴字第1號", year="109")})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["NTPC-1090011564"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")

    cases = provider.find_similar_cases(_info_with_sections(), screening, "原文")

    assert bart.retrieve.call_count == 6
    for c in bart.retrieve.call_args_list[-2:]:
        config = c.kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]
        assert "filter" not in config
    assert len(cases) == 1


def test_find_similar_cases_includes_cases_found_only_in_one_of_the_two_queries():
    """案例只在其中一路(理由)命中,另一路完全沒撈到,仍要進候選——RRF 是聯集不是交集。"""
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": [_case_chunk("NTPC-A", text="A")]},
        {"retrievalResults": [_case_chunk("NTPC-B", text="B")]},
    ]
    ddb = _PastDecisionDdb({"NTPC-A": _ddb_case("NTPC-A"), "NTPC-B": _ddb_case("NTPC-B")})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["NTPC-A", "NTPC-B"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")

    cases = provider.find_similar_cases(_info_with_sections(), screening, "原文")

    assert {c.case_no for c in cases} == {"A", "B"}


def test_find_similar_cases_reranks_and_caps_at_twenty_five():
    """RRF 合流後最多取前 25 案送進重排;find_similar_cases 回傳長度上限 25、已依重排排序。"""
    chunks = [_case_chunk(f"NTPC-{i:03d}") for i in range(30)]
    bart = MagicMock()
    bart.retrieve.side_effect = [{"retrievalResults": chunks}, {"retrievalResults": []}]
    items = {f"NTPC-{i:03d}": _ddb_case(f"NTPC-{i:03d}") for i in range(25)}
    ddb = _PastDecisionDdb(items)
    brt = MagicMock()
    brt.converse.return_value = _rerank_response([f"NTPC-{i:03d}" for i in range(25)])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")

    cases = provider.find_similar_cases(_info_with_sections(), screening, "原文")

    assert len(cases) == 25
    assert "029" not in [c.case_no for c in cases]


def test_find_similar_cases_marks_case_missing_from_dynamodb_instead_of_empty_shell():
    """DynamoDB 查不到就標示查不到:回一筆空欄位的卡片會讓「沒收錄」和「這案沒有爭點」長得一樣。"""
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": [_case_chunk("NTPC-9999999999")]},
        {"retrievalResults": []},
    ]
    ddb = _PastDecisionDdb({})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["NTPC-9999999999"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")

    cases = provider.find_similar_cases(_info_with_sections(), screening, "原文")

    assert len(cases) == 1
    assert cases[0].case_no == "NTPC-9999999999"
    assert "未查得" in cases[0].similarity_note
    assert cases[0].source_url is None


def test_find_similar_cases_raises_when_dynamodb_leaves_keys_unprocessed():
    """被限流而沒查成的鍵不得當成查無:那會讓資料層被限流長得像「這案沒收錄」。"""
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": [_case_chunk("NTPC-1121090455")]},
        {"retrievalResults": []},
    ]
    ddb = _PastDecisionDdb({"NTPC-1121090455": _ddb_case("NTPC-1121090455")}, unprocessed=True)
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")

    with pytest.raises(RuntimeError, match="未處理"):
        provider.find_similar_cases(_info_with_sections(), screening, "原文")


def test_find_similar_cases_dedupes_case_ids_before_batch_get():
    """RRF 排序本身以 case_id 去重,同一案的多段命中只算一次候選。"""
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": [_case_chunk("NTPC-1121090455", text=f"第{i}段") for i in range(3)]},
        {"retrievalResults": []},
    ]
    ddb = _PastDecisionDdb({"NTPC-1121090455": _ddb_case("NTPC-1121090455")})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["NTPC-1121090455"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")

    cases = provider.find_similar_cases(_info_with_sections(), screening, "原文")

    assert len(cases) == 1
    keys = ddb.batch_get_calls[0]["appeal_past_decisions"]["Keys"]
    assert keys == [{"case_id": "NTPC-1121090455"}]


def test_find_similar_cases_reads_law_ids_from_dynamodb_item():
    """有 law_ids 欄位的案子轉成 int 清單,缺欄位的案子回空清單——F2 的候選來源。"""
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": [_case_chunk("NTPC-A"), _case_chunk("NTPC-B")]},
        {"retrievalResults": []},
    ]
    ddb = _PastDecisionDdb(
        {
            "NTPC-A": _ddb_case("NTPC-A", law_ids=[5137, 5160]),
            "NTPC-B": _ddb_case("NTPC-B"),  # 缺 law_ids 欄位
        }
    )
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["NTPC-A", "NTPC-B"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")

    cases = provider.find_similar_cases(_info_with_sections(), screening, "原文")

    by_case_no = {c.case_no: c for c in cases}
    assert by_case_no["A"].law_ids == [5137, 5160]
    assert by_case_no["B"].law_ids == []


def test_find_similar_cases_source_url_and_key_pass_through_from_dynamodb():
    url = "https://web.law.ntpc.gov.tw/Scripts/Su_contents03.aspx?NO=3&EANO=1141021559"
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": [_case_chunk("NTPC-1141021559"), _case_chunk("NTPC-1141021560")]},
        {"retrievalResults": []},
    ]
    ddb = _PastDecisionDdb(
        {
            "NTPC-1141021559": _ddb_case("NTPC-1141021559", source_url=url),
            "NTPC-1141021560": _ddb_case("NTPC-1141021560", source_url="NTPC-1141021560"),  # 非網址
        }
    )
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["NTPC-1141021559", "NTPC-1141021560"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")

    cases = provider.find_similar_cases(_info_with_sections(), screening, "原文")

    assert [c.source_url for c in cases] == [url, None]


def test_generate_draft_strips_laws_not_in_allowed_list():
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response(
        "generate_draft",
        {
            "draft_type": "駁回",
            "fact": "事實",
            "reason": "理由",
            "main_text": "訴願駁回。",
            "cited_laws": ["廢棄物清理法#27", "自創法#999"],
        },
    )
    provider = _provider(bedrock_runtime=brt)
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


# ---------- get_law_articles:條號精查 ----------


def test_get_law_articles_returns_ref_from_dynamodb():
    ddb = MagicMock()
    ddb.batch_get_item.return_value = {
        "Responses": {
            "appeal_law_articles": [
                {
                    "law_article": "訴願法#77",
                    "law_name": "訴願法",
                    "article_no": "77",
                    "text": "訴願事件有左列各款情形之一者,應為不受理之決定:…",
                    "amend_date": "民國101年06月27日",
                    "source_key": "laws/訴願法.md",
                }
            ]
        }
    }
    provider = _provider(dynamodb_resource=ddb)

    refs = provider.get_law_articles(["訴願法#77"])

    assert len(refs) == 1
    assert refs[0].law_name == "訴願法"
    assert refs[0].article_no == "77"
    assert refs[0].amend_date == "民國101年06月27日"  # 來自 DynamoDB,非 LLM 生成
    assert refs[0].source_key == "laws/訴願法.md"


def test_get_law_articles_missing_key_falls_back_to_placeholder():
    ddb = MagicMock()
    ddb.batch_get_item.return_value = {"Responses": {"appeal_law_articles": []}}
    provider = _provider(dynamodb_resource=ddb)

    refs = provider.get_law_articles(["訴願法#77"])

    assert len(refs) == 1
    assert refs[0].law_name == "訴願法"
    assert refs[0].article_no == "77"
    assert refs[0].text == ""
    assert refs[0].amend_date == "未收錄"  # 查無資料填死值,不得由 LLM 生成


def test_get_law_articles_empty_keys_returns_empty():
    provider = _provider(dynamodb_resource=MagicMock())

    assert provider.get_law_articles([]) == []


def test_clause_to_appeal_article_accepts_chinese_numeral():
    """模型回中文數字時,F3 的 appeal_article filter 不能跟著失效。"""
    assert _clause_to_appeal_article("77條第二款") == "77(2)"
    assert _clause_to_appeal_article("77條第八款") == "77(8)"


def test_case_summary_drops_the_chunk_header_that_duplicates_the_listed_fields():
    """chunk 的【年度-案類-條款-結果】前綴是為了向量品質而加的,畫面上那幾欄已各自顯示,
    留在摘要裡只是重複又難讀。"""
    from app.providers.aws import case_summary

    crawled = "【113年-停車場法-§79-駁回】理由欄\n四、綜上論結,本件訴願為無理由,決定如主文。"
    official = "【110年-廢棄物清理法-77(2)-訴願逾期-不受理】主文欄\n訴願不受理。"

    assert case_summary(crawled) == "四、綜上論結,本件訴願為無理由,決定如主文。"
    assert case_summary(official) == "訴願不受理。"


def test_case_summary_cuts_on_a_sentence_boundary_not_mid_word():
    from app.providers.aws import case_summary

    text = "一、按訴願法第14條第1項規定。" + "二、本件訴願人不服原處分機關所為之處分。" * 20

    summary = case_summary(text)

    assert len(summary) <= 200
    assert summary.endswith("。")


def test_case_summary_without_any_sentence_end_still_returns_something_bounded():
    """整段沒有句號的 chunk(表格、條列殘段)不能因此變成空摘要——那會讓該筆案例看起來沒內容。"""
    from app.providers.aws import case_summary

    text = "甲" * 500

    summary = case_summary(text)

    assert 0 < len(summary) <= 200


# ---------- F2+:參考見解(三類各自 KB 檢索、依 ref_id 聚合、重排取 3) ----------


class _RefDdb:
    """假 DynamoDB:appeal_interpretations/appeal_rulings/appeal_judgments 皆以 ref_id 為主鍵。
    `unprocessed` 模擬被限流而沒查成的鍵,那與查無是兩回事。"""

    def __init__(self, items_by_ref_id: dict, unprocessed: bool = False):
        self._items = items_by_ref_id
        self._unprocessed = unprocessed
        self.batch_get_calls: list = []

    def batch_get_item(self, RequestItems):
        self.batch_get_calls.append(RequestItems)
        table = next(iter(RequestItems))
        keys = RequestItems[table]["Keys"]
        ids = [key["ref_id"] for key in keys]
        resp = {"Responses": {table: [self._items[i] for i in ids if i in self._items]}}
        if self._unprocessed:
            resp["UnprocessedKeys"] = {table: {"Keys": keys}}
        return resp


def _ref_chunk(ref_id: str, text: str, score: float = 1.0) -> dict:
    return {"content": {"text": text}, "metadata": {"ref_id": ref_id}, "score": score}


def _ddb_interp(ref_id: str, **overrides) -> dict:
    item = {
        "ref_id": ref_id,
        "doc_kind": "司法院釋字",
        "name": ref_id,
        "issuer": "",
        "issued_date": "民國87年11月20日",
        "topic": "怠於執行職務之國家賠償責任",
        "title": ref_id,
        "source_file": "",
        "s3_key": "",
        "source_url": "",
        "full_text": "解釋文全文",
        "chunk_count": 1,
        "chunk_ids": [f"{ref_id}#p1"],
    }
    item.update(overrides)
    return item


@pytest.fixture
def _ref_kb_settings(monkeypatch):
    """三類 KB id 全部設定好,模擬部署完成後的正常狀態。"""
    monkeypatch.setattr(settings, "KB_INTERPRETATION_ID", "KB-INTERP")
    monkeypatch.setattr(settings, "KB_RULING_ID", "KB-RULING")
    monkeypatch.setattr(settings, "KB_JUDGMENT_ID", "KB-JUDGMENT")


def test_find_references_skips_a_kind_whose_kb_id_is_unset(monkeypatch):
    """部署環境可能還沒設某一類 KB;空字串時跳過該類,不 raise,其餘類仍要跑完。"""
    monkeypatch.setattr(settings, "KB_INTERPRETATION_ID", "KB-INTERP")
    monkeypatch.setattr(settings, "KB_RULING_ID", "")
    monkeypatch.setattr(settings, "KB_JUDGMENT_ID", "")
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": [_ref_chunk("釋字第469號", "解釋文…")]}
    ddb = _RefDdb({"釋字第469號": _ddb_interp("釋字第469號")})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["釋字第469號"])

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    refs = provider.find_references(_info())

    assert [r.doc_kind for r in refs] == ["司法院釋字"]
    assert bart.retrieve.call_count == 1  # 只打了設了 KB id 的那一類


def test_find_references_queries_each_kind_kb_with_no_filter(_ref_kb_settings):
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": []}
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=_RefDdb({}))

    provider.find_references(_info())

    calls = bart.retrieve.call_args_list
    assert len(calls) == 3
    assert [c.kwargs["knowledgeBaseId"] for c in calls] == ["KB-INTERP", "KB-RULING", "KB-JUDGMENT"]
    for c in calls:
        config = c.kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]
        assert "filter" not in config
        assert config["numberOfResults"] == 30


def test_find_references_aggregates_multi_chunk_hits_of_the_same_document_by_score(_ref_kb_settings):
    """同一份文件的多片命中分數加總,代表整體相關度;應該排在只命中一片、單片分數更高的
    文件之前——聚合結果先送進重排,而不是逐片各自參賽。"""
    rows = [
        _ref_chunk("釋字第469號", "解釋文片段一", score=0.5),
        _ref_chunk("釋字第469號", "解釋文片段二", score=0.5),
        _ref_chunk("釋字第813號", "解釋文片段", score=0.9),
    ]
    bart = MagicMock()
    bart.retrieve.side_effect = [{"retrievalResults": rows}, {"retrievalResults": []}, {"retrievalResults": []}]
    ddb = _RefDdb({"釋字第469號": _ddb_interp("釋字第469號"), "釋字第813號": _ddb_interp("釋字第813號")})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["釋字第469號", "釋字第813號"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)

    refs = provider.find_references(_info())

    user_text = brt.converse.call_args_list[0].kwargs["messages"][0]["content"][0]["text"]
    assert user_text.index("釋字第469號") < user_text.index("釋字第813號")
    assert [r.name for r in refs] == ["釋字第469號", "釋字第813號"]


def test_find_references_reranks_and_caps_at_three(_ref_kb_settings):
    rows = [_ref_chunk(f"釋字第{n}號", f"解釋文{n}") for n in range(400, 415)]  # 15 篇不同文件
    bart = MagicMock()
    bart.retrieve.side_effect = [{"retrievalResults": rows}, {"retrievalResults": []}, {"retrievalResults": []}]
    ddb = _RefDdb({f"釋字第{n}號": _ddb_interp(f"釋字第{n}號") for n in range(400, 415)})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response([f"釋字第{n}號" for n in range(400, 415)])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)

    refs = provider.find_references(_info())

    assert len(refs) == 3


def test_find_references_each_kind_gets_its_own_kb_and_quota(_ref_kb_settings):
    """三類各自獨立 KB,互不排擠彼此的重排名額;共用一個前 N 名時,排序靠前的那類
    會把另兩類的分頁擠空,這裡驗證三類都各自取滿(或取盡)自己的上限。"""
    interp_rows = [_ref_chunk("釋字第469號", "解釋文")]
    ruling_rows = [_ref_chunk(f"法務部 法律字第{n}號", f"函釋{n}") for n in range(1, 6)]
    judgment_rows = [_ref_chunk("最高行政法院 102年度判字第147號", "判決")]
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": interp_rows},
        {"retrievalResults": ruling_rows},
        {"retrievalResults": judgment_rows},
    ]
    ddb = _RefDdb(
        {
            "釋字第469號": _ddb_interp("釋字第469號"),
            **{
                f"法務部 法律字第{n}號": _ddb_interp(
                    f"法務部 法律字第{n}號", doc_kind="行政函釋", issuer="法務部"
                )
                for n in range(1, 6)
            },
            "最高行政法院 102年度判字第147號": _ddb_interp(
                "最高行政法院 102年度判字第147號", doc_kind="行政法院裁判"
            ),
        }
    )
    brt = MagicMock()
    brt.converse.side_effect = [
        _rerank_response(["釋字第469號"]),
        _rerank_response([f"法務部 法律字第{n}號" for n in range(1, 6)]),
        _rerank_response(["最高行政法院 102年度判字第147號"]),
    ]
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)

    refs = provider.find_references(_info())

    by_kind = {
        k: [r.name for r in refs if r.doc_kind == k] for k in ("司法院釋字", "行政函釋", "行政法院裁判")
    }
    assert by_kind["司法院釋字"] == ["釋字第469號"]
    assert by_kind["行政法院裁判"] == ["最高行政法院 102年度判字第147號"]
    assert by_kind["行政函釋"] == [f"法務部 法律字第{n}號" for n in range(1, 4)]  # 每類自己的上限仍是 3


def test_find_references_fills_details_from_dynamodb_by_ref_id(_ref_kb_settings):
    """KB metadata 只帶 ref_id;名稱、發文機關、日期、原文出處一律由 DynamoDB 補。"""
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": [_ref_chunk("釋字第469號", "解釋文")]},
        {"retrievalResults": []},
        {"retrievalResults": []},
    ]
    item = _ddb_interp(
        "釋字第469號",
        s3_key="reference/司法院釋字/釋字第469號.pdf",
        source_url="https://law.moj.gov.tw/LawClass/ExContent.aspx?ty=C&CC=D&CNO=469",
    )
    ddb = _RefDdb({"釋字第469號": item})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["釋字第469號"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)

    (ref,) = provider.find_references(_info())

    assert ref.name == "釋字第469號"
    assert ref.topic == "怠於執行職務之國家賠償責任"
    assert ref.source_key == "reference/司法院釋字/釋字第469號.pdf"
    assert ref.source_url == "https://law.moj.gov.tw/LawClass/ExContent.aspx?ty=C&CC=D&CNO=469"


def test_find_references_marks_ref_missing_from_dynamodb_instead_of_dropping_it(_ref_kb_settings):
    """DynamoDB 查無時仍以 KB 資料組 ReferenceRef,不整批 raise——與 F3 查無案件的處置一致。"""
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": [_ref_chunk("釋字第999號", "解釋文")]},
        {"retrievalResults": []},
        {"retrievalResults": []},
    ]
    ddb = _RefDdb({})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["釋字第999號"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)

    (ref,) = provider.find_references(_info())

    assert ref.name == "釋字第999號"
    assert ref.source_key is None
    assert "未查得" in ref.relevance


def test_find_references_raises_when_dynamodb_leaves_ref_ids_unprocessed(_ref_kb_settings):
    """被限流而沒查成的 ref_id 不得當成查無:那會讓資料層被限流長得像「這份文件沒收錄」。"""
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": [_ref_chunk("釋字第469號", "解釋文")]},
        {"retrievalResults": []},
        {"retrievalResults": []},
    ]
    ddb = _RefDdb({"釋字第469號": _ddb_interp("釋字第469號")}, unprocessed=True)
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["釋字第469號"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)

    with pytest.raises(RuntimeError, match="未處理"):
        provider.find_references(_info())


def test_find_references_treats_a_none_chunk_text_as_empty_without_crashing(_ref_kb_settings):
    """KB 回傳的 content.text 鍵存在但值為 None 時(殘缺 chunk)不得 TypeError,
    該片段視為空文字參與聚合與候選文本組裝。"""
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {
            "retrievalResults": [
                {"content": {"text": None}, "metadata": {"ref_id": "釋字第469號"}, "score": 1.0}
            ]
        },
        {"retrievalResults": []},
        {"retrievalResults": []},
    ]
    ddb = _RefDdb({"釋字第469號": _ddb_interp("釋字第469號")})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["釋字第469號"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)

    (ref,) = provider.find_references(_info())

    assert ref.name == "釋字第469號"


def test_find_references_source_url_empty_string_falls_back_to_computed_url(_ref_kb_settings):
    """DynamoDB item 的 source_url 存空字串時視同未填,司法院釋字類別應由 validator
    補算出 law.moj 的解釋文網址,而不是原樣頂著一個空字串。"""
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": [_ref_chunk("釋字第469號", "解釋文")]},
        {"retrievalResults": []},
        {"retrievalResults": []},
    ]
    item = _ddb_interp("釋字第469號", source_url="")
    ddb = _RefDdb({"釋字第469號": item})
    brt = MagicMock()
    brt.converse.return_value = _rerank_response(["釋字第469號"])
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)

    (ref,) = provider.find_references(_info())

    assert ref.source_url == "https://law.moj.gov.tw/LawClass/ExContent.aspx?ty=C&CC=D&CNO=469"


def test_find_references_propagates_retrieval_failure_instead_of_returning_empty(_ref_kb_settings):
    """檢索爆掉不能吞成空清單:畫面上的「未檢索到相關參考見解」會同時代表查了沒有與查爆了。"""
    from botocore.exceptions import ClientError

    bart = MagicMock()
    bart.retrieve.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "rate exceeded"}}, "Retrieve"
    )
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=_RefDdb({}))

    with pytest.raises(ClientError):
        provider.find_references(_info())


def test_extract_case_info_carries_the_answer_document_fields():
    """訴願答辯書是第四份卷證,至今沒有任何欄位取自它,總匯表那一組永遠是空的。"""
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response(
        "extract_case_info",
        {
            "appellant": "王大明",
            "agency": "彰化縣環境保護局",
            "disposition_date": "110年3月5日",
            "disposition_no": "彰環廢字第1號",
            "disposition_summary": "裁處罰鍰",
            "case_type": "廢棄物清理",
            "answer_statement": "本件訴願駁回。",
            "answer_self_revoked": "否",
            "answer_arguments": ["訴願人確有任意棄置行為", "裁處於法有據"],
        },
    )
    info = _provider(bedrock_runtime=brt).extract_case_info("四份卷證原文")

    assert info.answer_statement == "本件訴願駁回。"
    assert info.answer_self_revoked == "否"
    assert info.answer_arguments == ["訴願人確有任意棄置行為", "裁處於法有據"]

    schema = brt.converse.call_args.kwargs["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    for key in ("answer_statement", "answer_self_revoked", "answer_arguments"):
        assert key in schema["properties"], key
        # required 要求的是「一定要回答這個鍵」,不是「內容不得為空」。原本列為選填的理由是
        # 「機關尚未答辯是常態」,但選填時模型會把三欄整組省略,即使答辯書內容不少。
        # 沒有答辯書時 prompt 要求明確回空值,那是誠實回報;整組不回才是看不出有沒有讀到。
        assert key in schema["required"], key


def test_extract_case_info_without_an_answer_document_leaves_those_fields_at_defaults():
    """機關受理後才送答辯書,收案當下本來就沒有;空的不得讓模型改變其他欄位的判斷。"""
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response(
        "extract_case_info",
        {
            "appellant": "李小華",
            "agency": "臺北市政府社會局",
            "disposition_date": "111年1月2日",
            "disposition_no": "北社字第2號",
            "disposition_summary": "駁回補助申請",
            "case_type": "社會救助",
        },
    )
    info = _provider(bedrock_runtime=brt).extract_case_info("只有三份卷證")

    assert info.answer_statement == ""
    assert info.answer_self_revoked == ""
    assert info.answer_arguments == []
    assert info.appellant == "李小華"  # 其他欄位不受影響


def test_extract_case_info_constrains_service_method_to_statutory_options():
    """兩個 provider 對同一欄位的值域必須一致——前端與答案鍵看到的形狀不該因模式而異。"""
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response(
        "extract_case_info",
        {"appellant": "王大明", "agency": "彰化縣環境保護局", "disposition_date": "110年3月5日", "disposition_no": "彰環廢字第1號", "disposition_summary": "裁處罰鍰", "case_type": "廢棄物清理"},
    )
    _provider(bedrock_runtime=brt).extract_case_info("訴願書原文")

    _, kwargs = brt.converse.call_args
    schema = kwargs["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert schema["properties"]["service_method"].get("enum") == list(SERVICE_METHODS)


def _draft_enum_sent(passed: bool) -> list[str]:
    """實際送進 Bedrock toolConfig 的值域;斷言要看送出去的那份。"""
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response(
        "generate_draft",
        {
            "draft_type": "駁回",
            "fact": "事實",
            "reason": "理由",
            "main_text": "訴願駁回。",
            "cited_laws": [],
        },
    )
    provider = _provider(bedrock_runtime=brt)
    screening = ScreeningResult(passed=passed, matched_clause=None, reasoning="x")
    provider.generate_draft(_info(), screening, [], [])
    _, kwargs = brt.converse.call_args
    schema = kwargs["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    return schema["properties"]["draft_type"]["enum"]


def test_generate_draft_schema_enum_follows_the_screening_verdict():
    """aws 與 local 共用同一條約束:受理案的 schema 不得提供「不受理」。"""
    from app.models import draft_types_for

    assert _draft_enum_sent(passed=True) == list(draft_types_for(passed=True))
    assert "不受理" not in _draft_enum_sent(passed=True)


def test_generate_draft_schema_enum_keeps_every_value_on_the_inadmissible_track():
    from app.models import DRAFT_TYPES

    assert _draft_enum_sent(passed=False) == list(DRAFT_TYPES)


def test_f1_schema_requires_every_field_a_procedural_check_reads():
    """與 local 同一條約束:schema 沒列必填,模型就不輸出該鍵,吃它的程式化檢核靜默停用。"""
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response("extract_case_info", {
        "appellant": "王大明", "agency": "機關", "disposition_date": "112年1月1日",
        "disposition_no": "字第1號", "disposition_summary": "罰鍰", "case_type": "廢棄物清理法",
    })
    provider = _provider(bedrock_runtime=brt)
    provider.extract_case_info("卷證全文")

    _, kwargs = brt.converse.call_args
    schema = kwargs["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    for field in ("disposition_recipient", "disposition_notice_clause", "receipt_date", "appeal_reasons"):
        assert field in schema["required"], field


def test_f1_schema_required_matches_every_case_info_field():
    """schema 的欄位清單推導自 CaseInfo 本身,新增欄位不必記得同步兩份手寫清單。"""
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response("extract_case_info", {
        "appellant": "王大明", "agency": "機關", "disposition_date": "112年1月1日",
        "disposition_no": "字第1號", "disposition_summary": "罰鍰", "case_type": "廢棄物清理法",
    })
    provider = _provider(bedrock_runtime=brt)
    provider.extract_case_info("卷證全文")

    _, kwargs = brt.converse.call_args
    schema = kwargs["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]

    assert set(schema["required"]) == set(CaseInfo.model_fields.keys())


def test_f1_schema_constrains_agent_role_to_statutory_options():
    schema = case_info_json_schema()
    assert schema["properties"]["agent_role"]["enum"] == list(AGENT_ROLES)
    assert schema["properties"]["service_method"]["enum"] == list(SERVICE_METHODS)


def test_generate_draft_schema_requires_gist():
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response(
        "generate_draft",
        {
            "draft_type": "駁回",
            "fact": "事實",
            "reason": "理由",
            "main_text": "訴願駁回。",
            "cited_laws": [],
            "gist": "因違反廢棄物清理法事件提起訴願",
        },
    )
    provider = _provider(bedrock_runtime=brt)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="x")

    draft = provider.generate_draft(_info(), screening, [], [])

    assert draft.gist == "因違反廢棄物清理法事件提起訴願"
    _, kwargs = brt.converse.call_args
    schema = kwargs["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert "gist" in schema["properties"]
    assert "gist" in schema["required"]
