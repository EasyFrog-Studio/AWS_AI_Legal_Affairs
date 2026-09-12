"""AWSProvider 單元測試:mock boto3 client,驗證參數組裝,不做真實呼叫。"""
from unittest.mock import MagicMock

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
        kept = [r for r in rows if _matches(cond, r["metadata"])]
        return {"retrievalResults": kept[: config["numberOfResults"]]}

    return _retrieve


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


def test_recommend_laws_retrieves_statutes_only_never_empty_shell_refs():
    """KB-LAW 同時裝著法規與官方函釋/判解,後者沒有 law_name/article_no。F2 的 filter 若
    只排除普通法,那些 chunk 會一起被撈出來,鍵全組成 "#" 互相覆蓋,承辦人看到「 第  條」。"""
    rows = [
        {
            "content": {"text": "廢棄物清理法第27條全文"},
            "metadata": {
                "law_name": "廢棄物清理法",
                "article_no": "27",
                "amend_date": "民國106年01月18日",
                "law_type": "實體法",
                "doc_kind": "法規",
            },
        },
        {
            "content": {"text": "民法第148條全文"},
            "metadata": {
                "law_name": "民法",
                "article_no": "148",
                "amend_date": "民國110年01月20日",
                "law_type": "普通法",
                "doc_kind": "法規",
            },
        },
        {
            "content": {"text": "內政部函釋全文"},
            "metadata": {"law_type": "其他", "doc_kind": "行政函釋"},
        },
        {
            "content": {"text": "釋字第469號解釋全文"},
            "metadata": {"law_type": "其他", "doc_kind": "判解"},
        },
    ]
    bart = MagicMock()
    bart.retrieve.side_effect = _filtering_retrieve(rows)

    provider = _provider(bedrock_agent_runtime=bart)
    laws = provider.recommend_laws(_info(cited_articles=[]))

    assert [(l.law_name, l.article_no) for l in laws] == [("廢棄物清理法", "27")]


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
    assert filter_["andAll"] == [
        {"equals": {"key": "doc_kind", "value": "法規"}},
        {"notEquals": {"key": "law_type", "value": "普通法"}},
    ]

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


def test_recommend_laws_skips_placeholder_cited_article_without_querying_dynamodb():
    """F1 對抽不到條號的欄位填「未載明」,這種假條號不該送進 DynamoDB 精查,也不該出現在結果裡。"""
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": []}
    ddb = MagicMock()

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb)
    laws = provider.recommend_laws(_info(cited_articles=["廢棄物清理法#未載明"]))

    assert laws == []
    ddb.batch_get_item.assert_not_called()


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


def test_recommend_laws_keeps_retrieved_entry_when_ddb_metadata_disagrees_with_key():
    """精查結果須以請求的 key 落位——DB 的 law_name/article_no 與 key 不符時,不得覆蓋檢索結果。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [
            {
                "content": {"text": "檢索到的條文"},
                "metadata": {
                    "law_name": "廢棄物清理法",
                    "article_no": "27",
                    "amend_date": "民國106年01月18日",
                },
            }
        ]
    }
    ddb = MagicMock()
    # law_article 是「訴願法#77」,但 metadata 卻寫成廢清法#27(資料髒)
    ddb.batch_get_item.return_value = {
        "Responses": {
            "appeal_law_articles": [
                {
                    "law_article": "訴願法#77",
                    "law_name": "廢棄物清理法",
                    "article_no": "27",
                    "text": "髒資料",
                    "amend_date": "民國101年06月27日",
                }
            ]
        }
    }
    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb)

    refs = provider.recommend_laws(_info(cited_articles=["訴願法#77"]))

    # 兩筆都要在:檢索到的廢清法#27 沒有被精查結果擠掉
    assert len(refs) == 2
    assert refs[0].text == "檢索到的條文"


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

# ---------- 推薦筆數上限 ----------
def _vector_config(bart, call_index):
    kwargs = bart.retrieve.call_args_list[call_index].kwargs
    return kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]


def test_recommend_laws_retrieves_three_results():
    bart = MagicMock()
    bart.retrieve.return_value = {"retrievalResults": []}

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=MagicMock())
    provider.recommend_laws(_info(cited_articles=[]))

    assert _vector_config(bart, 0)["numberOfResults"] == 3


def test_find_similar_cases_returns_at_most_three_distinct_cases():
    """一份決定書切成多個 chunk,靠 numberOfResults 湊不出三件不同案號,須以案號去重後截斷。"""

    def _result(case_no, section="事實"):
        return {
            "content": {"text": f"【{section}】{case_no} 本件訴願人不服原處分…"},
            "metadata": {
                "case_no": case_no,
                "year": "112",
                "case_type": "廢棄物清理",
                "appeal_article": "",
                "issue": "任意棄置",
                "result": "駁回",
                "source_file": f"{case_no}.pdf",
            },
        }

    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [
            _result("112-0001", "事實"),
            _result("112-0001", "理由"),
            _result("112-0002"),
            _result("112-0003"),
            _result("112-0004"),
        ]
    }
    provider = _provider(bedrock_agent_runtime=bart)

    cases = provider.find_similar_cases(
        _info(), ScreeningResult(passed=True, matched_clause=None, reasoning="通過"), "原文"
    )

    assert [c.case_no for c in cases] == ["112-0001", "112-0002", "112-0003"]
    # chunk 取用量要大於呈現筆數,否則同一案號的多個段落會把三件不同案例佔滿
    assert _vector_config(bart, 0)["numberOfResults"] > 3


def _kb_law_result(article_no="27"):
    return {
        "content": {"text": f"廢棄物清理法第{article_no}條全文"},
        "metadata": {
            "law_name": "廢棄物清理法",
            "article_no": article_no,
            "amend_date": "民國106年01月18日",
            "law_type": "實體法",
            "source_file": "markdown/相關法規/廢棄物清理法.md",
        },
    }


def test_recommend_laws_returns_at_most_three_entries():
    """降到三條是呈現上限,不是只管檢索那一段:精查補進來的引用條號也算在內。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [_kb_law_result("27"), _kb_law_result("50"), _kb_law_result("12")]
    }
    ddb = MagicMock()
    ddb.batch_get_item.return_value = {"Responses": {"appeal_law_articles": []}}

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb)
    laws = provider.recommend_laws(_info(cited_articles=["行政罰法#7", "訴願法#77"]))

    assert len(laws) == 3


def test_the_article_the_appeal_itself_cites_survives_the_cap():
    """引用條號被擠掉的代價不只是少一條推薦——F4 的可引用清單是從這裡組出來的,
    掉了就等於決定書不能引訴願人自己援引的那一條。"""
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [_kb_law_result("27"), _kb_law_result("50"), _kb_law_result("12")]
    }
    ddb = MagicMock()
    ddb.batch_get_item.return_value = {
        "Responses": {
            "appeal_law_articles": [
                {"law_article": "訴願法#77", "law_name": "訴願法", "article_no": "77",
                 "text": "訴願事件有左列各款情形之一者…", "amend_date": "民國101年06月27日"}
            ]
        }
    }

    provider = _provider(bedrock_agent_runtime=bart, dynamodb_resource=ddb)
    laws = provider.recommend_laws(_info(cited_articles=["訴願法#77"]))

    assert len(laws) == 3
    assert "訴願法#77" in [f"{l.law_name}#{l.article_no}" for l in laws]


# ---------- F2+ 參考見解(find_references) ----------

_REF_ROWS = [
    {
        "content": {"text": "廢棄物清理法第27條全文"},
        "metadata": {
            "law_name": "廢棄物清理法",
            "article_no": "27",
            "amend_date": "民國106年01月18日",
            "law_type": "實體法",
            "doc_kind": "法規",
        },
    },
    {
        "content": {"text": "釋字第469號解釋文…"},
        "metadata": {
            "law_name": "釋字第469號",
            "article_no": "",
            "law_type": "其他",
            "doc_kind": "司法院釋字",
            "topic": "怠於執行職務之國家賠償責任",
            "source_file": "markdown/司法院釋字/釋字第469號解釋-國家賠償請求權.md",
        },
    },
    {
        "content": {"text": "法務部函釋說明…"},
        "metadata": {
            "law_name": "法務部 法律字第1000002151號",
            "article_no": "",
            "law_type": "其他",
            "doc_kind": "行政函釋",
            "issuer": "法務部",
            "amend_date": "民國 100 年 03 月 30 日",
        },
    },
]


def test_find_references_excludes_statutes_and_maps_heterogeneous_kinds():
    """釋字有題旨無發文機關無日期,函釋有發文機關有日期無題旨——同一組欄位對應要兩種都撐得住。"""
    bart = MagicMock()
    bart.retrieve.side_effect = _filtering_retrieve(_REF_ROWS)
    brt = MagicMock()

    refs = _provider(bedrock_agent_runtime=bart, bedrock_runtime=brt).find_references(_info())

    assert [r.doc_kind for r in refs] == ["司法院釋字", "行政函釋"]

    yizi, hanshi = refs
    assert yizi.name == "釋字第469號"
    assert yizi.topic == "怠於執行職務之國家賠償責任"
    assert yizi.issuer == ""
    assert yizi.issued_date == "未收錄"  # metadata 無日期,不得由 LLM 補
    assert yizi.source_key == "markdown/司法院釋字/釋字第469號解釋-國家賠償請求權.md"

    assert hanshi.name == "法務部 法律字第1000002151號"
    assert hanshi.issuer == "法務部"
    assert hanshi.issued_date == "民國 100 年 03 月 30 日"
    assert hanshi.topic == ""
    assert hanshi.source_key is None  # 爬蟲來源沒有 markdown,前端據此不畫「原文」鈕

    brt.converse.assert_not_called()  # F2+ 純檢索,不經 LLM


def test_find_references_dedupes_chunks_of_the_same_document():
    """一份長判決被切成多筆 chunk,畫面上只該出現一則。"""
    chunks = [
        {
            "content": {"text": f"判決全文第{i}段"},
            "metadata": {
                "law_name": "最高行政法院 102年度判字第147號",
                "doc_kind": "行政法院裁判",
                "issuer": "最高行政法院",
                "law_type": "其他",
            },
        }
        for i in (1, 2, 3)
    ]
    bart = MagicMock()
    bart.retrieve.side_effect = _filtering_retrieve(chunks)

    refs = _provider(bedrock_agent_runtime=bart).find_references(_info())

    assert len(refs) == 1
    assert refs[0].name == "最高行政法院 102年度判字第147號"


def test_find_references_skips_rows_without_a_name():
    """認不出是哪一份文件的列,寧可不給——比照相似案例檢索缺案號時的處置。"""
    rows = [
        {"content": {"text": "來源不明"}, "metadata": {"doc_kind": "行政函釋", "law_type": "其他"}},
        _REF_ROWS[1],
    ]
    bart = MagicMock()
    bart.retrieve.side_effect = _filtering_retrieve(rows)

    refs = _provider(bedrock_agent_runtime=bart).find_references(_info())

    assert [r.name for r in refs] == ["釋字第469號"]


def test_find_references_caps_at_three():
    rows = [
        {
            "content": {"text": f"釋字第{n}號解釋文"},
            "metadata": {"law_name": f"釋字第{n}號", "doc_kind": "司法院釋字", "law_type": "其他"},
        }
        for n in range(400, 410)
    ]
    bart = MagicMock()
    bart.retrieve.side_effect = _filtering_retrieve(rows)

    assert len(_provider(bedrock_agent_runtime=bart).find_references(_info())) == 3


def test_find_references_propagates_retrieval_failure_instead_of_returning_empty():
    """檢索爆掉不能吞成空清單:畫面上的「未檢索到相關參考見解」會同時代表查了沒有與查爆了。"""
    from botocore.exceptions import ClientError

    bart = MagicMock()
    bart.retrieve.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "rate exceeded"}}, "Retrieve"
    )

    try:
        _provider(bedrock_agent_runtime=bart).find_references(_info())
    except ClientError:
        return
    raise AssertionError("檢索失敗必須往外傳,不得回空清單")


def test_find_references_overfetches_so_one_long_document_cannot_starve_the_list():
    """一份長判決切成多筆 chunk,若只撈 3 筆就去重,畫面上只會剩一則——與 F3 同一種失效。"""
    rows = [
        {
            "content": {"text": f"最高行政法院判決第{i}段"},
            "metadata": {
                "law_name": "最高行政法院 102年度判字第147號",
                "doc_kind": "行政法院裁判",
                "law_type": "其他",
            },
        }
        for i in (1, 2, 3)
    ] + [
        {
            "content": {"text": "釋字第469號解釋文"},
            "metadata": {"law_name": "釋字第469號", "doc_kind": "司法院釋字", "law_type": "其他"},
        },
        {
            "content": {"text": "法務部函釋"},
            "metadata": {
                "law_name": "法務部 法律字第1000002151號",
                "doc_kind": "行政函釋",
                "law_type": "其他",
            },
        },
    ]
    bart = MagicMock()
    bart.retrieve.side_effect = _filtering_retrieve(rows)

    refs = _provider(bedrock_agent_runtime=bart).find_references(_info())

    # 逐類檢索後,結果依 _REF_DOC_KINDS 的類別次序分組,不再是單一檢索的相關性次序
    assert [r.name for r in refs] == [
        "釋字第469號",
        "法務部 法律字第1000002151號",
        "最高行政法院 102年度判字第147號",
    ]


def test_find_references_routes_a_crawled_pdf_to_the_archived_store():
    """爬蟲語料的 source_file 是 PDF 檔名,取原文端點的 markdown 那一支找不到它;
    但那份 PDF 就在本機存檔目錄裡,改指過去(`reference/<類別>/<檔名>`)——
    原本一律回 None 的結果是 F2+ 在畫面上一個可點的來源都沒有。"""
    rows = [
        {
            "content": {"text": "釋字第718號解釋文"},
            "metadata": {
                "law_name": "釋字第718號",
                "doc_kind": "司法院釋字",
                "law_type": "其他",
                "source_file": "釋字第0718號_103-03-21_集會遊行法申請許可規定.pdf",
            },
        },
        {
            "content": {"text": "內政部函釋"},
            "metadata": {
                "law_name": "內政部 內授營建管字第1000810874號",
                "doc_kind": "行政函釋",
                "law_type": "其他",
                "source_file": "markdown/行政函釋/內政部100年12月9日內授營建管字第1000810874號函釋-場所區隔方式.md",
            },
        },
    ]
    bart = MagicMock()
    bart.retrieve.side_effect = _filtering_retrieve(rows)

    yizi, hanshi = _provider(bedrock_agent_runtime=bart).find_references(_info())

    assert yizi.source_key == "reference/司法院釋字/釋字第0718號_103-03-21_集會遊行法申請許可規定.pdf"
    assert hanshi.source_key == rows[1]["metadata"]["source_file"]  # 官方語料仍走 markdown


def test_retrieval_paths_drop_source_keys_the_source_endpoint_cannot_serve():
    """F2 法規與 F3 案例的 source_file 同樣混著取原文端點服務不到的值(爬蟲法規 4,133 筆、
    爬蟲決定書的內部編號),與參考見解是同一個問題,判斷要一致。"""
    law_rows = [
        {
            "content": {"text": "某法第5條"},
            "metadata": {
                "law_name": "某法",
                "article_no": "5",
                "doc_kind": "法規",
                "law_type": "實體法",
                "source_file": "法條-某法.jsonl",
            },
        }
    ]
    bart = MagicMock()
    bart.retrieve.side_effect = _filtering_retrieve(law_rows)
    (law,) = _provider(bedrock_agent_runtime=bart).recommend_laws(_info(cited_articles=[]))
    assert law.source_key is None

    case_rows = [
        {
            "content": {"text": "【事實】本件訴願人不服原處分…"},
            "metadata": {
                "case_no": "NTPC-1131090247",
                "year": "113",
                "case_type": "廢棄物清理",
                "result": "駁回",
                "source_file": "NTPC-1131090247",
            },
        },
        {
            "content": {"text": "【事實】另一件…"},
            "metadata": {
                "case_no": "彰府訴字第9號",
                "year": "112",
                "case_type": "廢棄物清理",
                "result": "駁回",
                "source_file": "markdown/歷史訴願決定書/02.112年-違反廢棄物清理法事件.md",
            },
        },
    ]
    bart2 = MagicMock()
    bart2.retrieve.side_effect = _filtering_retrieve(case_rows)
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="通過")
    crawl, official = _provider(bedrock_agent_runtime=bart2).find_similar_cases(
        _info(), screening, "原文"
    )
    assert crawl.source_key is None
    assert official.source_key == "markdown/歷史訴願決定書/02.112年-違反廢棄物清理法事件.md"


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


def test_similar_cases_carry_the_source_url_like_local_does():
    """aws 與 local 的 F3 必須等價:其中一邊帶得出原始來源網址、另一邊帶不出來,
    等於同一件案子在兩個模式下看到不一樣的東西。"""
    url = "https://web.law.ntpc.gov.tw/Scripts/Su_contents03.aspx?NO=3&EANO=1141021559"
    bart = MagicMock()
    bart.retrieve.return_value = {
        "retrievalResults": [
            {
                "content": {"text": "相似案例全文"},
                "metadata": {
                    "case_no": "1141021559",
                    "year": "114",
                    "case_type": "廢棄物清理",
                    "appeal_article": "77(2)",
                    "issue": "逾期提起",
                    "result": "不受理",
                    "source_url": url,
                },
            },
            {
                "content": {"text": "另一件相似案例"},
                "metadata": {
                    "case_no": "1141021560",
                    "year": "114",
                    "case_type": "廢棄物清理",
                    "appeal_article": "77(2)",
                    "issue": "逾期提起",
                    "result": "不受理",
                    "source_url": "NTPC-1141021560",  # 非網址,不得畫成連結
                },
            },
        ]
    }
    provider = _provider(bedrock_agent_runtime=bart)
    screening = ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期")

    cases = provider.find_similar_cases(_info(), screening, "原文")

    assert [c.source_url for c in cases] == [url, None]


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


def test_find_references_gives_each_kind_its_own_quota():
    """三類共用一個前 3 名時,排序靠前的那一類會把另兩類洗掉,畫面上那兩個分頁就是空的。
    逐類各撈各的:函釋滿額不影響釋字與裁判各自被撈到。"""
    rows = [
        {
            "content": {"text": f"法務部函釋第{n}號"},
            "metadata": {
                "law_name": f"法務部 法律字第{n}號",
                "doc_kind": "行政函釋",
                "law_type": "其他",
            },
        }
        for n in range(1, 6)
    ] + [
        {
            "content": {"text": "釋字第469號解釋文"},
            "metadata": {"law_name": "釋字第469號", "doc_kind": "司法院釋字", "law_type": "其他"},
        },
        {
            "content": {"text": "最高行政法院判決"},
            "metadata": {
                "law_name": "最高行政法院 102年度判字第147號",
                "doc_kind": "行政法院裁判",
                "law_type": "其他",
            },
        },
    ]
    bart = MagicMock()
    bart.retrieve.side_effect = _filtering_retrieve(rows)

    refs = _provider(bedrock_agent_runtime=bart).find_references(_info())

    by_kind = {
        k: [r.name for r in refs if r.doc_kind == k]
        for k in ("司法院釋字", "行政函釋", "行政法院裁判")
    }
    assert by_kind["司法院釋字"] == ["釋字第469號"]
    assert by_kind["行政法院裁判"] == ["最高行政法院 102年度判字第147號"]
    assert by_kind["行政函釋"] == [f"法務部 法律字第{n}號" for n in range(1, 4)]  # 每類自己的上限仍是 3


def test_find_references_returns_nothing_for_a_doc_kind_without_a_tab():
    """三類是封閉集合:前端一類一個分頁,檢索也只認這三類。語料多出第四類時它不會悄悄
    混進某一頁,而是整批不出現——要收它就得先給它一個分頁。"""
    rows = [
        {
            "content": {"text": "訴願答辯書內容"},
            "metadata": {"law_name": "某答辯書", "doc_kind": "訴願答辯書", "law_type": "其他"},
        },
        {
            "content": {"text": "釋字第469號解釋文"},
            "metadata": {"law_name": "釋字第469號", "doc_kind": "司法院釋字", "law_type": "其他"},
        },
    ]
    bart = MagicMock()
    bart.retrieve.side_effect = _filtering_retrieve(rows)

    refs = _provider(bedrock_agent_runtime=bart).find_references(_info())

    assert [r.name for r in refs] == ["釋字第469號"]
