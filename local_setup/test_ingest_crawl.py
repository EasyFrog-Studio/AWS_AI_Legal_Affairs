import json

import ingest_crawl
from ingest_crawl import bucket_of, clause_to_appeal_article, map_case_row, map_law_row, roc_amend_date


def test_77_clause_becomes_the_appeal_article_format_used_by_case_chunks():
    assert clause_to_appeal_article("§77(2)") == "77(2)"
    assert clause_to_appeal_article("§77(8)") == "77(8)"


def test_clauses_that_carry_no_77_item_number_yield_no_appeal_article():
    # F3 只以 77(N) 硬過濾;§79/§81、複合與未判定值填進去只會製造查不到的假鍵
    assert clause_to_appeal_article("§79") == ""
    assert clause_to_appeal_article("§79+§81") == ""
    assert clause_to_appeal_article("§77+§79/§81") == ""
    assert clause_to_appeal_article("§77(未標明)") == ""
    assert clause_to_appeal_article("未判定") == ""
    assert clause_to_appeal_article("") == ""


def test_western_revised_date_becomes_the_roc_format_the_official_corpus_uses():
    # 20180801 對應官方空氣污染防制法 chunk 實際帶的 amend_date 字串
    assert roc_amend_date("20180801") == "民國 107 年 08 月 01 日"
    assert roc_amend_date("20251226") == "民國 114 年 12 月 26 日"


def test_unparsable_revised_date_falls_back_to_the_not_recorded_marker():
    # amend_date 禁止臆造;抽不出日期就要留下「查無」的痕跡而非猜一個
    assert roc_amend_date("") == "未收錄"
    assert roc_amend_date("2025") == "未收錄"
    assert roc_amend_date("民國114年") == "未收錄"


CASE_ROW = {
    "片段名": "NTPC-1131021559-理由-003",
    "內容": "四、綜上論結,本件訴願為程序不合,依訴願法第 77 條第 2 款規定,決定如主文。",
    "metadata": {
        "source_file": "NTPC-1131021559", "year": "113", "case_type": "噪音管制法",
        "clause": "§77(2)", "result": "不受理", "case_no": "1131021559",
        "section": "理由", "paragraph_role": "結語",
        "source_url": "https://web.law.ntpc.gov.tw/x", "related_laws": "訴願法 第14條",
    },
}


def test_a_crawled_decision_chunk_maps_onto_the_case_chunks_contract():
    row = map_case_row(CASE_ROW, "噪音管制法")
    assert row["id"] == "NTPC-1131021559-理由-003"
    m = row["metadata"]
    assert m["appeal_article"] == "77(2)"          # F3 不受理路徑硬過濾用
    assert (m["case_type"], m["result"], m["year"]) == ("噪音管制法", "不受理", "113")
    assert m["case_no"] == "1131021559"            # 缺此欄 F3 會整筆跳過
    assert m["section"] == "理由"
    assert m["issue"] == ""                        # 爬蟲語料沒有爭點欄,留空不臆造
    assert "噪音管制法" in row["text"] and "綜上論結" in row["text"]


def test_a_dismissed_case_on_a_non_77_clause_maps_with_an_empty_appeal_article():
    src = json.loads(json.dumps(CASE_ROW))
    src["片段名"] = "NTPC-1121000001-事實-001"
    src["內容"] = "訴願人於民國 112 年間經原處分機關裁處罰鍰。"
    src["metadata"].update({"clause": "§79", "result": "駁回", "section": "事實", "year": "112"})
    row = map_case_row(src, "空氣污染防制法")
    assert row["metadata"]["appeal_article"] == ""
    assert row["metadata"]["result"] == "駁回"
    assert row["metadata"]["section"] == "事實"


def test_holdout_year_decisions_are_refused_by_the_mapper():
    src = json.loads(json.dumps(CASE_ROW))
    src["metadata"]["year"] = "114"
    assert map_case_row(src, "噪音管制法") is None


def law_row(law_name, article, content, deleted="false", revised="20251226"):
    return {
        "片段名": f"LAW-{law_name}-{article}", "來源檔": law_name, "內容": content,
        "metadata": {"law_name": law_name, "article": article, "revised_date": revised,
                     "deleted": deleted, "doc_type": "法規"},
    }


def test_a_crawled_procedure_law_article_maps_onto_the_law_chunks_contract():
    row = map_law_row(law_row("行政訴訟法", "4", "人民因中央或地方機關之違法行政處分…"))
    assert row["id"] == "行政訴訟法#4"          # law_articles 精查鍵格式
    m = row["metadata"]
    assert (m["law_name"], m["article_no"]) == ("行政訴訟法", "4")
    assert m["law_type"] == "程序法"
    assert m["amend_date"] == "民國 114 年 12 月 26 日"


def test_a_crawled_domain_statute_is_classified_as_substantive_so_f2_keeps_it():
    row = map_law_row(law_row("都市計畫法", "81", "直轄市、縣(市)政府為擬定都市計畫…", revised="20210602"))
    assert row["metadata"]["law_type"] == "實體法"   # F2 只排除普通法,實體法必須留下
    assert row["metadata"]["amend_date"] == "民國 110 年 06 月 02 日"


def test_deleted_articles_are_refused_so_they_never_reach_f2():
    assert map_law_row(law_row("水污染防治法", "9", "(刪除)", deleted="true")) is None


def test_laws_already_verified_in_the_official_corpus_are_refused():
    # 官方 11 部的條數與修正日期已驗算過,爬蟲版同鍵會覆寫掉,一律不收
    assert map_law_row(law_row("建築法", "25", "建築物非經申請直轄市…")) is None
    assert map_law_row(law_row("民法", "1", "民事,法律所未規定者,依習慣…")) is None


def test_reference_chunks_are_loaded_as_is_because_they_are_already_native_schema(tmp_path, monkeypatch):
    ref_dir = tmp_path / "參考資料"
    ref_dir.mkdir()
    (ref_dir / "司法院釋字.jsonl").write_text(
        json.dumps({"id": "釋字第1號", "text": "【司法院釋字】釋字第1號\n解釋文…",
                    "metadata": {"doc_kind": "司法院釋字", "law_type": "其他",
                                 "law_name": "釋字第1號", "article_no": "",
                                 "amend_date": "民國 38 年 01 月 06 日"}}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (ref_dir / "行政函釋.jsonl").write_text(
        json.dumps({"id": "法務部 法律字第1000002151號", "text": "【行政函釋】…",
                    "metadata": {"doc_kind": "行政函釋", "law_type": "其他",
                                 "law_name": "法務部 法律字第1000002151號", "article_no": "",
                                 "amend_date": "民國 100 年 03 月 30 日"}}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(ingest_crawl, "CRAWL_DIR", tmp_path)

    rows = ingest_crawl.reference_rows()
    assert len(rows) == 2
    assert {r["metadata"]["doc_kind"] for r in rows} == {"司法院釋字", "行政函釋"}
    # 全數 article_no 為空,正是它們不能進 law_articles 精查表(鍵為 法名#條號)的理由
    assert all(r["metadata"]["article_no"] == "" for r in rows)


def test_missing_reference_directory_yields_no_rows_rather_than_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_crawl, "CRAWL_DIR", tmp_path)
    assert ingest_crawl.reference_rows() == []


def test_the_case_type_bucket_comes_from_the_jsonl_filename():
    assert bucket_of("決定書-空氣污染防制法.jsonl") == "空氣污染防制法"
    assert bucket_of("決定書-其他案型.jsonl") == "其他案型"


def test_the_free_text_case_reason_is_replaced_by_the_bucket_and_kept_as_a_subtype():
    # 來源系統的 case_type 是自由文字案由(語料共 630 種),F3 的完全相等過濾對它必然落空
    src = json.loads(json.dumps(CASE_ROW))
    src["metadata"]["case_type"] = "空氣污染防制法、水污染防治法及廢棄物清理法"
    row = map_case_row(src, "空氣污染防制法")
    assert row["metadata"]["case_type"] == "空氣污染防制法"
    assert row["metadata"]["case_subtype"] == "空氣污染防制法、水污染防治法及廢棄物清理法"
    # 送進 embedding 的 text 沿用原始案由,既有向量才不會與重跑結果不一致
    assert "空氣污染防制法、水污染防治法及廢棄物清理法" in row["text"]


def test_a_second_bucket_maps_independently_of_the_first():
    src = json.loads(json.dumps(CASE_ROW))
    src["metadata"]["case_type"] = "廢止工廠登記"
    row = map_case_row(src, "其他案型")
    assert row["metadata"]["case_type"] == "其他案型"
    assert row["metadata"]["case_subtype"] == "廢止工廠登記"
