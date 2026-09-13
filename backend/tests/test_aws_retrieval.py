"""aws_retrieval 通用小工具的單元測試:_clip / _rrf / rerank_ids 的邊界行為。
provider 層的整合行為(F2/F3/F2+ 實際檢索與重排)見 test_aws_provider.py。
"""
from unittest.mock import MagicMock

import pytest

from app.providers.aws import AWSProvider
from app.providers.aws_retrieval import _clip, _rrf, rerank_ids


def _provider(**clients) -> AWSProvider:
    return AWSProvider(
        bedrock_runtime=clients.get("bedrock_runtime", MagicMock()),
        bedrock_agent_runtime=clients.get("bedrock_agent_runtime", MagicMock()),
        dynamodb_resource=clients.get("dynamodb_resource", MagicMock()),
    )


def _toolUse_response(tool_name: str, input_data: dict) -> dict:
    return {"output": {"message": {"content": [{"toolUse": {"name": tool_name, "input": input_data}}]}}}


# ---------- _clip ----------


def test_clip_truncates_to_n_chars():
    assert _clip("一二三四五", 3) == "一二三"


def test_clip_handles_none_and_short_text():
    assert _clip(None, 10) == ""
    assert _clip("短", 10) == "短"


# ---------- _rrf ----------


def test_rrf_returns_empty_for_no_rankings():
    assert _rrf([]) == []
    assert _rrf([[], []]) == []


def test_rrf_favors_an_id_ranked_in_both_lists_over_one_ranked_in_only_one():
    """A 在兩路都排第 2 名,B 只在其中一路排第 1 名:A 的分數是兩次 1/(k+2) 相加,
    仍可能贏過 B 單一一次 1/(k+1)——RRF 讓「多路都支持」壓過「單路排名靠前」。"""
    ranking1 = ["B", "A"]
    ranking2 = ["A"]
    fused = _rrf([ranking1, ranking2], k=1)
    # A: 1/(1+1+1) + 1/(1+0+1) = 1/3 + 1/2 = 0.8333; B: 1/(1+0+1) = 0.5
    assert fused[0] == "A"
    assert fused[1] == "B"


def test_rrf_only_scores_ids_that_appear_in_at_least_one_ranking():
    fused = _rrf([["X", "Y"], ["Z"]])
    assert set(fused) == {"X", "Y", "Z"}


# ---------- rerank_ids ----------


def test_rerank_ids_returns_empty_without_calling_the_llm_when_no_candidates():
    brt = MagicMock()
    provider = _provider(bedrock_runtime=brt)

    result = rerank_ids(provider, "task", "context", [], top_n=3)

    assert result == []
    brt.converse.assert_not_called()


def test_rerank_ids_dedupes_and_appends_missing_candidates_in_original_order():
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response("rerank", {"ranked_ids": ["b", "b", "a"]})
    provider = _provider(bedrock_runtime=brt)
    candidates = [("a", "文本a"), ("b", "文本b"), ("c", "文本c")]

    result = rerank_ids(provider, "task", "context", candidates, top_n=3)

    # "b" 去重只算一次,"a" 排第二;"c" 沒被回傳,依原順序補在最後
    assert result == ["b", "a", "c"]


def test_rerank_ids_falls_back_to_original_order_when_nothing_returned_resolves():
    """回傳格式不合(全不在候選內)時沿用向量檢索順序,不得整批掛掉。"""
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response("rerank", {"ranked_ids": ["不存在的id", "也不存在"]})
    provider = _provider(bedrock_runtime=brt)
    candidates = [("a", "文本a"), ("b", "文本b")]

    result = rerank_ids(provider, "task", "context", candidates, top_n=3)

    assert result == ["a", "b"]


def test_rerank_ids_falls_back_to_original_order_when_ranked_ids_is_empty():
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response("rerank", {"ranked_ids": []})
    provider = _provider(bedrock_runtime=brt)
    candidates = [("a", "文本a"), ("b", "文本b")]

    result = rerank_ids(provider, "task", "context", candidates, top_n=3)

    assert result == ["a", "b"]


def test_rerank_ids_truncates_to_top_n():
    brt = MagicMock()
    brt.converse.return_value = _toolUse_response("rerank", {"ranked_ids": ["c", "b", "a"]})
    provider = _provider(bedrock_runtime=brt)
    candidates = [("a", "文本a"), ("b", "文本b"), ("c", "文本c")]

    result = rerank_ids(provider, "task", "context", candidates, top_n=2)

    assert result == ["c", "b"]


def test_rerank_ids_propagates_llm_exceptions():
    """LLM 呼叫本身的例外(逾時、限流…)一律往上拋,不得吞掉。"""
    brt = MagicMock()
    brt.converse.side_effect = RuntimeError("Bedrock 呼叫失敗")
    provider = _provider(bedrock_runtime=brt)
    candidates = [("a", "文本a")]

    with pytest.raises(RuntimeError, match="Bedrock 呼叫失敗"):
        rerank_ids(provider, "task", "context", candidates, top_n=3)
