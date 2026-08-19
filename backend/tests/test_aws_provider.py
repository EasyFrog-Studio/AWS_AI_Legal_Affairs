"""AWSProvider 單元測試:mock boto3 client,驗證參數組裝,不做真實呼叫。"""
from unittest.mock import MagicMock

from app.models import CaseInfo, ScreeningResult, LawRef
from app.providers.aws import AWSProvider, _clause_to_appeal_article


def _provider(**clients) -> AWSProvider:
    return AWSProvider(
        bedrock_runtime=clients.get("bedrock_runtime", MagicMock()),
        bedrock_agent_runtime=clients.get("bedrock_agent_runtime", MagicMock()),
        dynamodb_resource=clients.get("dynamodb_resource", MagicMock()),
    )


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


def test_screening_prompt_embeds_full_article_77_text():
    from app.providers.aws import _load_prompt

    text = _load_prompt("screening.txt")
    assert "行政處分已不存在者" in text
    assert "對於非行政處分或其他依法不屬訴願救濟範圍內之事項提起訴願者" in text


def test_recommend_laws_filter_excludes_general_law_and_does_not_call_llm():
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [
            {
                "content": {"text": "廢棄物清理法第27條全文"},
                "metadata": {
                    "law_name": "廢棄物清理法",
                    "article_no": "27",
                    "amend_date": "民國106年01月18日",
                    "law_type": "實體法",
                    "source_file": "markdown/相關法規/廢棄物清理法.md",
                },
            }
        ]
    }
    ddb = MagicMock()
    ddb.batch_get_item.return_value = {"Responses": {"appeal_law_articles": []}}
    brt = MagicMock()

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb, bedrock_runtime=brt)
    laws = provider.recommend_laws(_info())

    _, kwargs = bart.retrieve.call_args
    filter_ = kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
    assert filter_ == {"notEquals": {"key": "law_type", "value": "普通法"}}

    assert len(laws) == 1
    assert laws[0].amend_date == "民國106年01月18日"
    # F2 不應呼叫 LLM(converse),修正日期不得由 LLM 生成
    brt.converse.assert_not_called()


def test_recommend_laws_missing_cited_article_falls_back_to_未收錄():
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": []}
    ddb = MagicMock()
    ddb.batch_get_item.return_value = {"Responses": {"appeal_law_articles": []}}

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb)
    laws = provider.recommend_laws(_info(cited_articles=["廢棄物清理法#99"]))

    assert len(laws) == 1
    assert laws[0].amend_date == "未收錄"
    assert laws[0].law_name == "廢棄物清理法"
    assert laws[0].article_no == "99"


def test_recommend_laws_batches_dynamodb_batch_get_item_at_100():
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": []}
    ddb = MagicMock()
    ddb.batch_get_item.return_value = {"Responses": {"appeal_law_articles": []}}

    cited = [f"某法#{i}" for i in range(101)]
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb)
    provider.recommend_laws(_info(cited_articles=cited))

    assert ddb.batch_get_item.call_count == 2
    first_keys = ddb.batch_get_item.call_args_list[0].kwargs["RequestItems"]["appeal_law_articles"]["Keys"]
    second_keys = ddb.batch_get_item.call_args_list[1].kwargs["RequestItems"]["appeal_law_articles"]["Keys"]
    assert len(first_keys) == 100
    assert len(second_keys) == 1


def test_find_similar_cases_admissible_filter_is_case_type_only():
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": []}
    provider = _provider(bedrock_agent_runtime=bart)

    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過審查")
    provider.find_similar_cases(_info(), screening, "原文")

    # 三層 fallback:case_type filter → case_type filter(放寬)→ 純語意(無 filter)
    calls = bart.retrieve.call_args_list
    assert len(calls) == 3
    first = calls[0].kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
    assert first == {"equals": {"key": "case_type", "value": "廢棄物清理"}}
    last = calls[-1].kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]
    assert "filter" not in last


def test_find_similar_cases_inadmissible_filter_includes_result_and_appeal_article():
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [
            {
                "content": {"text": "相似案例全文"},
                "metadata": {
                    "case_no": "北市訴字第1號",
                    "year": "109",
                    "case_type": "社會救助",
                    "appeal_article": "77(2)",
                    "issue": "逾期提起",
                    "result": "不受理",
                    "source_file": "markdown/歷史訴願決定書/北市訴字第1號.md",
                },
            }
        ]
    }
    provider = _provider(bedrock_agent_runtime=bart)
    screening = ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期")
    info = _info(case_type="社會救助")

    cases = provider.find_similar_cases(info, screening, "原文")

    _, kwargs = bart.retrieve.call_args
    filter_ = kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
    assert filter_ == {
        "andAll": [
            {"equals": {"key": "case_type", "value": "社會救助"}},
            {"equals": {"key": "result", "value": "不受理"}},
            {"equals": {"key": "appeal_article", "value": "77(2)"}},
        ]
    }
    assert len(cases) == 1
    assert cases[0].case_no == "北市訴字第1號"


def test_find_similar_cases_retries_with_relaxed_filter_when_empty():
    bart = MagicMock()
    bart.retrieve.side_effect = [
        {"retrievalResults": []},
        {
            "retrievalResults": [
                {
                    "content": {"text": "相似案例全文"},
                    "metadata": {
                        "case_no": "彰府訴字第1號",
                        "year": "109",
                        "case_type": "廢棄物清理",
                        "appeal_article": "無",
                        "issue": "任意棄置",
                        "result": "駁回",
                        "source_file": "x.md",
                    },
                }
            ]
        },
    ]
    provider = _provider(bedrock_agent_runtime=bart)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")

    cases = provider.find_similar_cases(_info(), screening, "原文")

    assert bart.retrieve.call_count == 2
    second_filter = bart.retrieve.call_args_list[1].kwargs["retrievalConfiguration"][
        "vectorSearchConfiguration"
    ]["filter"]
    assert second_filter == {"equals": {"key": "case_type", "value": "廢棄物清理"}}
    assert len(cases) == 1


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
