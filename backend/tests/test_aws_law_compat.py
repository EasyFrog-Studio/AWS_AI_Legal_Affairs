"""法條精查相容層:DDB_LAW_INDEX 切換 batch_get_item(主鍵表)與 GSI query(索引表)兩條路徑。"""
import pytest
from boto3.dynamodb.conditions import Key

from app.config import settings
from app.providers.aws import AWSProvider


def _provider(ddb) -> AWSProvider:
    from unittest.mock import MagicMock

    return AWSProvider(
        bedrock_runtime=MagicMock(),
        bedrock_agent_runtime=MagicMock(),
        dynamodb_resource=ddb,
    )


@pytest.fixture(autouse=True)
def _restore_settings():
    """每個測試各自設定 DDB_LAW_INDEX / DDB_LAW_DATE_FIELD,測完還原,避免互相汙染。"""
    index_before = settings.DDB_LAW_INDEX
    date_field_before = settings.DDB_LAW_DATE_FIELD
    yield
    settings.DDB_LAW_INDEX = index_before
    settings.DDB_LAW_DATE_FIELD = date_field_before


class _BatchGetOnlyDdb:
    """模擬主鍵表:只實作 batch_get_item,記錄收到的 Keys。"""

    def __init__(self, items_by_key: dict):
        self._items_by_key = items_by_key
        self.batch_get_calls: list[dict] = []

    def batch_get_item(self, RequestItems):
        self.batch_get_calls.append(RequestItems)
        table_name = next(iter(RequestItems))
        keys = [k["law_article"] for k in RequestItems[table_name]["Keys"]]
        items = [self._items_by_key[k] for k in keys if k in self._items_by_key]
        return {"Responses": {table_name: items}}


class _GsiTable:
    def __init__(self, items_by_key: dict, query_calls: list, raise_on_query: bool = False):
        self._items_by_key = items_by_key
        self._query_calls = query_calls
        self._raise_on_query = raise_on_query

    def query(self, IndexName, KeyConditionExpression, Limit=None):
        """比對整個 KeyConditionExpression 物件而不是拆它的內部欄位:拆內部結構會讓 boto3 改版把測試弄紅。"""
        if self._raise_on_query:
            raise RuntimeError("dynamodb query failed")
        self._query_calls.append({"IndexName": IndexName, "condition": KeyConditionExpression})
        hit = [item for key, item in self._items_by_key.items()
               if Key("law_article").eq(key) == KeyConditionExpression]
        return {"Items": hit[:1]}


class _GsiDdb:
    """模擬索引表:只實作 Table(...).query,記錄每次呼叫。"""

    def __init__(self, items_by_key: dict, raise_on_query: bool = False):
        self.query_calls: list = []
        self._table = _GsiTable(items_by_key, self.query_calls, raise_on_query=raise_on_query)

    def Table(self, name):
        return self._table


def test_batch_get_path_when_index_unset():
    settings.DDB_LAW_INDEX = ""
    ddb = _BatchGetOnlyDdb(
        {
            "訴願法#77": {"law_article": "訴願法#77", "law_name": "訴願法", "article_no": "77", "text": "…", "amend_date": "民國101年06月27日"},
            "道路交通管理處罰條例#12": {"law_article": "道路交通管理處罰條例#12", "law_name": "道路交通管理處罰條例", "article_no": "12", "text": "…", "amend_date": "民國105年01月06日"},
        }
    )
    provider = _provider(ddb)

    refs = provider.get_law_articles(["訴願法#77", "道路交通管理處罰條例#12"])

    assert len(ddb.batch_get_calls) == 1
    keys_sent = {k["law_article"] for k in ddb.batch_get_calls[0]["appeal_law_articles"]["Keys"]}
    assert keys_sent == {"訴願法#77", "道路交通管理處罰條例#12"}
    by_key = {r.article_no: r for r in refs}
    assert by_key["77"].law_name == "訴願法"
    assert by_key["12"].law_name == "道路交通管理處罰條例"


def test_gsi_query_path_when_index_set():
    settings.DDB_LAW_INDEX = "law-article-index"
    keys = ["訴願法#77", "道路交通管理處罰條例#12"]
    ddb = _GsiDdb(
        {
            "訴願法#77": {"law_article": "訴願法#77", "law_name": "訴願法", "article_no": "77", "text": "…", "amend_date": "民國101年06月27日"},
            "道路交通管理處罰條例#12": {"law_article": "道路交通管理處罰條例#12", "law_name": "道路交通管理處罰條例", "article_no": "12", "text": "…", "amend_date": "民國105年01月06日"},
        }
    )
    provider = _provider(ddb)

    refs = provider.get_law_articles(keys)

    assert len(ddb.query_calls) == len(keys)
    assert all(call["IndexName"] == "law-article-index" for call in ddb.query_calls)
    sent = [call["condition"] for call in ddb.query_calls]
    assert sent == [Key("law_article").eq(k) for k in keys]
    by_key = {r.article_no: r for r in refs}
    assert by_key["77"].law_name == "訴願法"
    assert by_key["12"].law_name == "道路交通管理處罰條例"


def test_date_field_is_configurable():
    settings.DDB_LAW_INDEX = "law-article-index"
    item = {
        "law_article": "訴願法#77",
        "law_name": "訴願法",
        "article_no": "77",
        "text": "…",
        "amend_date": "民國101年06月27日",
        "revised_date": "民國110年12月01日",
    }
    ddb = _GsiDdb({"訴願法#77": item})

    settings.DDB_LAW_DATE_FIELD = "amend_date"
    provider = _provider(ddb)
    refs_amend = provider.get_law_articles(["訴願法#77"])
    assert refs_amend[0].amend_date == "民國101年06月27日"

    settings.DDB_LAW_DATE_FIELD = "revised_date"
    provider = _provider(_GsiDdb({"訴願法#77": item}))
    refs_revised = provider.get_law_articles(["訴願法#77"])
    assert refs_revised[0].amend_date == "民國110年12月01日"


def test_missing_item_falls_back():
    settings.DDB_LAW_INDEX = "law-article-index"
    ddb = _GsiDdb({})
    provider = _provider(ddb)

    refs = provider.get_law_articles(["訴願法#77"])

    assert len(refs) == 1
    assert refs[0].text == ""
    assert refs[0].amend_date == "未收錄"
    assert "未查得" in refs[0].relevance


def test_query_error_propagates():
    settings.DDB_LAW_INDEX = "law-article-index"
    ddb = _GsiDdb({}, raise_on_query=True)
    provider = _provider(ddb)

    with pytest.raises(RuntimeError):
        provider.get_law_articles(["訴願法#77"])
