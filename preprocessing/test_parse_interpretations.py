"""parse_interpretations 純函式測試(不讀 PDF、不連 DB)。
執行:cd preprocessing && python -m pytest test_parse_interpretations.py
"""
from parse_interpretations import build_rows

HANSHI = "內政部100年12月9日內授營建管字第1000810874號函釋-場所區隔方式"
YIZI = "釋字第469號解釋-怠於執行職務之國家賠償責任"
CAIPAN = "最高行政法院102年度判字第147號行政判決-政府資訊公開法精神"


def test_hanshi_metadata_matches_the_crawl_corpus_contract():
    """官方與爬蟲兩批參考語料共用一組欄位名,檢索端才不必認兩種形狀。"""
    (row,) = build_rows(HANSHI, "函釋內容")
    m = row["metadata"]
    assert m["doc_kind"] == "行政函釋"
    assert m["law_name"] == "內政部 內授營建管字第1000810874號"
    assert m["article_no"] == ""
    assert m["amend_date"] == "民國 100 年 12 月 09 日"
    assert m["issuer"] == "內政部"
    assert m["topic"] == "場所區隔方式"
    assert m["law_type"] == "其他"


def test_yizi_and_judgment_split_into_distinct_doc_kinds():
    """官方「司法院釋字及行政判解」一個資料夾混裝兩種文件,不能再共用「判解」這個值。"""
    yizi = build_rows(YIZI, "解釋文")[0]["metadata"]
    assert yizi["doc_kind"] == "司法院釋字"
    assert yizi["law_name"] == "釋字第469號"
    assert yizi["topic"] == "怠於執行職務之國家賠償責任"
    assert "issuer" not in yizi  # 釋字無發文機關,不留空字串佔位

    caipan = build_rows(CAIPAN, "判決全文")[0]["metadata"]
    assert caipan["doc_kind"] == "行政法院裁判"
    assert caipan["law_name"] == "最高行政法院 102年度判字第147號"
    assert caipan["issuer"] == "最高行政法院"


def test_undated_filenames_omit_amend_date_rather_than_invent_one():
    """官方釋字與裁判的檔名不帶日期。缺就是缺,由檢索端填「未收錄」,前處理不得臆造。"""
    for stem in (YIZI, CAIPAN):
        assert "amend_date" not in build_rows(stem, "內容")[0]["metadata"]


def test_source_file_points_at_the_markdown_the_source_endpoint_can_serve():
    """取原文的端點只在前處理輸出目錄下找 markdown;填 PDF 檔名一律落到「找不到檔案」。"""
    assert build_rows(HANSHI, "x")[0]["metadata"]["source_file"] == (
        f"markdown/行政函釋/{HANSHI}.md"
    )
    assert build_rows(YIZI, "x")[0]["metadata"]["source_file"] == f"markdown/司法院釋字/{YIZI}.md"


def test_chunk_text_carries_the_new_doc_kind_prefix():
    assert build_rows(YIZI, "解釋文")[0]["text"].startswith(f"【司法院釋字】{YIZI}\n")


def test_long_document_splits_into_numbered_chunks_sharing_one_metadata():
    long_text = "\n\n".join(f"第{i}段。" * 120 for i in range(6))
    rows = build_rows(CAIPAN, long_text)
    assert len(rows) > 1
    assert [r["id"] for r in rows] == [f"{CAIPAN[:60]}#p{i}" for i in range(1, len(rows) + 1)]
    assert all(r["metadata"] == rows[0]["metadata"] for r in rows)


def test_unparseable_filename_yields_no_rows():
    """檔名解析不出來就沒有 law_name,硬收進去會變成撈得到卻顯示不出識別字串的一筆。"""
    assert build_rows("看不出是哪一種文件的檔名", "內容") == []
