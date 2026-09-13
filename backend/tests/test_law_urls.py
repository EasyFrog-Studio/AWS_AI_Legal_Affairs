"""法規原文連結:法規名稱 -> 全國法規資料庫網址。推不出來一律回 None,不給死連結。"""
from app.law_urls import law_article_url


def test_an_indexed_law_with_an_article_links_to_that_article():
    for law_name, article_no in (("廢棄物清理法", "46"), ("建築法", "25")):
        url = law_article_url(law_name, article_no)
        assert url.startswith("https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=")
        assert url.endswith(f"&flno={article_no}")


def test_a_sub_numbered_article_keeps_its_dash():
    """「46-1」是條次細分,不是兩條;拆掉會連到錯的條文。"""
    assert law_article_url("廢棄物清理法", "46-1").endswith("&flno=46-1")


def test_a_law_without_an_article_links_to_the_whole_law():
    url = law_article_url("廢棄物清理法", "")
    assert url.startswith("https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=")
    assert "flno" not in url


def test_a_law_outside_the_index_gets_no_link():
    """對照表只收語料實際用到的法規;查不到就不畫連結——猜一個 pcode 會連到別部法。"""
    for law_name in ("新北市自治條例", "不存在的法", "", None):
        assert law_article_url(law_name, "1") is None


def test_a_whitespace_padded_name_still_resolves():
    """chunk metadata 的法規名稱偶有前後空白,那不是另一部法。"""
    assert law_article_url("  廢棄物清理法 ", "46") == law_article_url("廢棄物清理法", "46")


# ---------- LawRef 自己算得出連結,不必在每個組裝點記得填 ----------


def test_law_ref_exposes_the_article_url():
    """F2 檢索、不受理法源精查、DynamoDB 補全都會生 LawRef,逐點填 URL 遲早漏一個;
    連結是 (law_name, article_no) 的純函式,讓 model 自己算。"""
    from app.models import LawRef

    ref = LawRef(
        law_name="廢棄物清理法", article_no="46", text="條文", amend_date="民國 114 年", relevance="命中"
    )
    assert ref.source_url.endswith("&flno=46")
    assert ref.model_dump()["source_url"] == ref.source_url


def test_a_law_ref_outside_the_index_has_no_url():
    from app.models import LawRef

    ref = LawRef(
        law_name="不存在的法", article_no="1", text="條文", amend_date="民國 114 年", relevance="命中"
    )
    assert ref.source_url is None
    assert ref.model_dump()["source_url"] is None


def test_a_law_ref_explicit_source_url_wins_over_the_computed_fallback():
    """DynamoDB item 帶了 source_url 就直接用它,不被 pcode 對照表算出來的另一個網址蓋掉。"""
    from app.models import LawRef

    ref = LawRef(
        law_name="廢棄物清理法",
        article_no="46",
        text="條文",
        amend_date="民國 114 年",
        relevance="命中",
        source_url="https://example.gov.tw/explicit",
    )
    assert ref.source_url == "https://example.gov.tw/explicit"


# ---------- F2+ 參考見解:釋字推得出連結,函釋與裁判推不出來 ----------


def _ref(doc_kind: str, name: str):
    from app.models import ReferenceRef

    return ReferenceRef(
        doc_kind=doc_kind, name=name, issued_date="民國 87 年", text="內容", relevance="命中"
    )


def test_a_judicial_interpretation_links_by_its_number():
    """釋字的網址只靠號數就推得出來(全國法規資料庫 ExContent);
    司法院自己那套 id 是內部流水號,推不出來——試過 id=310829 回的是釋字第648號,不是469。"""
    for name, number in (("釋字第469號", "469"), ("釋字第 1 號", "1"), ("釋字第813號", "813")):
        assert _ref("司法院釋字", name).source_url == (
            f"https://law.moj.gov.tw/LawClass/ExContent.aspx?ty=C&CC=D&CNO={number}"
        )


def test_interpretations_and_judgments_get_no_url():
    """行政函釋各部會來源不一、行政法院裁判要完整案號參數,兩類都推不出穩定網址。
    與其給一個點進去要再找一次的搜尋頁,不如留白(同 external_source_url 的判準)。"""
    for doc_kind, name in (
        ("行政函釋", "法務部 法律字第1000002151號"),
        ("行政法院裁判", "最高行政法院 87年度判字第427號"),
        ("司法院釋字", "釋字令人看不懂的名稱"),
    ):
        assert _ref(doc_kind, name).source_url is None


def test_a_reference_ref_explicit_source_url_wins_over_the_computed_fallback():
    """DynamoDB item 帶了 source_url(如函釋、裁判)就直接用它,不受 interpretation_url 只認
    司法院釋字這件事限制。"""
    from app.models import ReferenceRef

    ref = ReferenceRef(
        doc_kind="行政函釋",
        name="法務部 法律字第1000002151號",
        issued_date="民國 100 年",
        text="內容",
        relevance="命中",
        source_url="https://example.gov.tw/ruling",
    )
    assert ref.source_url == "https://example.gov.tw/ruling"
