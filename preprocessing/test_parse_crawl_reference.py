from parse_crawl_reference import build_rows, is_official, parse_caipan, parse_hanshi, parse_yizi


def test_files_copied_in_from_the_official_dataset_are_recognised():
    # 這些檔的內容已由 parse_interpretations.py 收進 law_chunks,再收一次就是重複列
    assert is_official("釋字第0469號_官方_釋字第469號解釋-公法上請求權.pdf")
    assert is_official("092_官方_最高行政法院92年度判字第275號判決-行政程序法第6條.pdf")


def test_crawled_files_are_not_mistaken_for_official_ones():
    assert not is_official("釋字第0001號_038-01-06_立委就任官吏時仍保有立委職位？.pdf")
    assert not is_official("民國100年03月30日_法務部 法律字第1000002151號_行政函釋.pdf")
    assert not is_official("087-03-19_最高行政法院_87年度判字第427號.pdf")


def test_yizi_filename_yields_the_unpadded_number_and_the_publication_date():
    got = parse_yizi("釋字第0001號_038-01-06_立委就任官吏時仍保有立委職位？")
    assert got["law_name"] == "釋字第1號"        # 官方 chunk 用「釋字第469號」不補零,對齊
    assert got["amend_date"] == "民國 38 年 01 月 06 日"
    assert got["topic"] == "立委就任官吏時仍保有立委職位？"
    assert parse_yizi("釋字第0812號_110-12-10_刑法、竊盜犯贓物犯保安處分條例及組織犯罪防制條例")["law_name"] == "釋字第812號"


def test_hanshi_filename_yields_the_issuing_agency_and_document_number():
    got = parse_hanshi("民國100年03月30日_法務部 法律字第1000002151號_行政函釋")
    assert got["issuer"] == "法務部"
    assert got["law_name"] == "法務部 法律字第1000002151號"
    assert got["amend_date"] == "民國 100 年 03 月 30 日"
    other = parse_hanshi("民國108年12月12日_行政院環境保護署 環署水字第1080094227號_行政函釋")
    assert other["issuer"] == "行政院環境保護署"
    assert other["amend_date"] == "民國 108 年 12 月 12 日"


def test_caipan_filename_yields_the_court_and_case_number():
    got = parse_caipan("087-03-19_最高行政法院_87年度判字第427號")
    assert got["issuer"] == "最高行政法院"
    assert got["law_name"] == "最高行政法院 87年度判字第427號"
    assert got["amend_date"] == "民國 87 年 03 月 19 日"
    other = parse_caipan("115-08-19_臺灣基隆地方法院_114年度訴字第305號")
    assert other["issuer"] == "臺灣基隆地方法院"
    assert other["amend_date"] == "民國 115 年 08 月 19 日"


def test_a_filename_that_matches_no_pattern_is_reported_empty_rather_than_guessed():
    # 解析不出來要讓主流程數得出來,不能塞一個編出來的標題進檢索庫
    assert parse_yizi("釋字-無編號") == {}
    assert parse_hanshi("函釋_沒有日期") == {}
    assert parse_caipan("裁判_沒有法院") == {}


def test_a_short_reference_document_becomes_one_law_chunk_row():
    rows = build_rows("司法院釋字", "釋字第0001號_038-01-06_立委就任官吏時仍保有立委職位？",
                      "解釋文\n立法委員依憲法第七十五條之規定不得兼任官吏。", parse_yizi)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "釋字第1號"
    assert row["text"].startswith("【司法院釋字】釋字第1號")
    assert "不得兼任官吏" in row["text"]
    m = row["metadata"]
    assert m["law_type"] == "其他"          # F2 只排除普通法,其他一律可檢索
    assert m["doc_kind"] == "司法院釋字"
    assert m["law_name"] == "釋字第1號"
    assert m["amend_date"] == "民國 38 年 01 月 06 日"


def test_a_long_reference_document_is_split_into_numbered_chunks():
    long_text = "\n\n".join(f"第{i}段。" + "本院認為原處分並無違誤。" * 40 for i in range(12))
    rows = build_rows("行政法院裁判", "087-03-19_最高行政法院_87年度判字第427號", long_text, parse_caipan)
    assert len(rows) > 1
    assert [r["id"] for r in rows][:2] == ["最高行政法院 87年度判字第427號#p1", "最高行政法院 87年度判字第427號#p2"]
    assert all(r["metadata"]["law_name"] == "最高行政法院 87年度判字第427號" for r in rows)
    assert len({r["id"] for r in rows}) == len(rows)


def test_a_document_whose_filename_cannot_be_parsed_produces_no_rows():
    assert build_rows("行政函釋", "函釋_沒有日期", "任何內容", parse_hanshi) == []
