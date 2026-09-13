"""F2/F2+/F3 檢索改版:F3 案類先找案例並重排 25 件、F2 以那 25 件的 law_id 為候選再重排取 3、
F2+ 三類各自 KB 檢索 + 文件聚合 + 重排取 3。
aws.py 的三個方法只做薄委派,把 self(provider)傳進來使用其既有私有成員(_retrieve/_converse_json/_ddb 等)。
"""
import logging
from typing import Optional

from app.config import settings
from app.models import CaseInfo, LawRef, ReferenceRef, ScreeningResult, SimilarCase
from app.providers.aws import (
    _REF_DOC_KINDS,
    _clause_to_appeal_article,
    _law_id_of,
    _load_prompt,
    _retrieval_query,
    _valid_cited_articles,
    case_summary,
    external_source_url,
    viewable_source_key,
)

logger = logging.getLogger(__name__)

# 實測上限:in filter 值 100 個字串、numberOfResults=100、查詢文字 1500 字皆可;以下取保守值,
# 回傳筆數只受 numberOfResults 封頂
_LAW_CANDIDATE_CAP = 50  # F2 候選 law_id 的 in filter 上限
_CASE_CHUNK_FETCH = 100  # F3 兩路查詢各自撈取的 chunk 數
_QUERY_MAX_CHARS = 900  # Bedrock Retrieve 查詢文字上限 1000,留餘裕截 900

_CASE_TOP_N = 25  # F3 RRF 合流後、重排的候選案件數,亦為 find_similar_cases 的回傳上限
_RRF_K = 60

_LAW_RETRIEVE_N = 25  # F2 KB-LAW 檢索筆數(候選 law_id 或全庫皆同)
_LAW_TOP_N = 3  # F2 重排後呈現上限

_REF_FETCH = 30  # F2+ 每類 KB 檢索筆數
_REF_AGG_TOP = 10  # F2+ 每類依 ref_id 聚合後,送進重排的候選數
_REF_TOP_N = 3  # F2+ 每類重排後呈現上限

_KB_ID_BY_DOC_KIND = {
    "司法院釋字": "KB_INTERPRETATION_ID",
    "行政函釋": "KB_RULING_ID",
    "行政法院裁判": "KB_JUDGMENT_ID",
}
_DDB_TABLE_BY_DOC_KIND = {
    "司法院釋字": "DDB_INTERPRETATION_TABLE",
    "行政函釋": "DDB_RULING_TABLE",
    "行政法院裁判": "DDB_JUDGMENT_TABLE",
}


def _clip(text: Optional[str], n: int) -> str:
    return (text or "")[:n]


def _rrf(rankings: list[list[str]], k: int = _RRF_K) -> list[str]:
    """Reciprocal Rank Fusion:多路排序 -> 單一排序。id 在某一路排第 r 名(0-based)得分 1/(k+r+1),
    跨路分數相加;未進某一路排序的 id 在該路不計分,不是罰分。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda item_id: scores[item_id], reverse=True)


def rerank_ids(
    provider,
    task: str,
    query_context: str,
    candidates: list[tuple[str, str]],
    top_n: int,
) -> list[str]:
    """sonnet 重排:candidates 為 (id, 候選文本) 列,回傳依相關性排序、去重、長度至多 top_n 的 id 清單。
    LLM 回傳的 id 必須是候選子集,去重後依原順序把漏掉的候選補在後面;格式不合(空、全不在候選內)
    時整份沿用向量順序並 warning;呼叫本身的例外(含 bedrock_gate 之外的任何錯誤)一律往上拋,
    不得吞掉——案件因此落 error 好過安靜地用一份沒被排序過的結果。"""
    if not candidates:
        return []
    candidate_ids = [cid for cid, _ in candidates]
    listing = "\n".join(f"[{cid}] {text}" for cid, text in candidates)
    schema = {
        "type": "object",
        "properties": {"ranked_ids": {"type": "array", "items": {"type": "string"}}},
        "required": ["ranked_ids"],
    }
    user_text = f"【任務】{task}\n\n【查詢脈絡】\n{query_context}\n\n【候選清單】\n{listing}"
    data = provider._converse_json(_load_prompt("rerank.txt"), user_text, "rerank", schema)

    seen: set[str] = set()
    ranked: list[str] = []
    for cid in data.get("ranked_ids") or []:
        if cid in candidate_ids and cid not in seen:
            ranked.append(cid)
            seen.add(cid)
    if not ranked:
        logger.warning("重排回傳未命中任何候選 id，沿用向量檢索順序：%r", data.get("ranked_ids"))

    remainder = [cid for cid in candidate_ids if cid not in seen]
    return (ranked + remainder)[:top_n]


# ---------- F3:相似案例(案類找案例,兩路查詢 RRF 合流,重排 25 件) ----------


def _case_filter_tiers(info: CaseInfo, screening: ScreeningResult) -> list[Optional[dict]]:
    """三層 fallback,沿用改版前 aws.py 的既有邏輯(受理案第一、二層皆為 case_type,
    第三層去 filter;不受理案逐層卸除 appeal_article -> result -> 全去)。"""
    if screening.passed:
        case_type_filter = {"equals": {"key": "case_type", "value": info.case_type}}
        return [case_type_filter, case_type_filter, None]

    clauses = [
        {"equals": {"key": "case_type", "value": info.case_type}},
        {"equals": {"key": "result", "value": "不受理"}},
    ]
    appeal_article = _clause_to_appeal_article(screening.matched_clause)
    if appeal_article:
        clauses.append({"equals": {"key": "appeal_article", "value": appeal_article}})
    return [
        {"andAll": clauses},
        {"equals": {"key": "case_type", "value": info.case_type}},
        {"equals": {"key": "result", "value": "不受理"}},
    ]


def _case_ranking(chunks: list[dict]) -> list[str]:
    """chunk 列(已依向量分數排序) -> case_id 排序,依首次出現位置去重。"""
    seen: set[str] = set()
    ranking: list[str] = []
    for chunk in chunks:
        case_id = (chunk.get("metadata") or {}).get("case_id")
        if case_id and case_id not in seen:
            seen.add(case_id)
            ranking.append(case_id)
    return ranking


def find_similar_cases(
    provider, info: CaseInfo, screening: ScreeningResult, text: str
) -> list[SimilarCase]:
    facts_query = _clip("\n".join(info.appeal_facts), _QUERY_MAX_CHARS) or _retrieval_query(info)
    reasons_query = _clip("\n".join(info.appeal_reasons), _QUERY_MAX_CHARS) or _retrieval_query(info)

    facts_chunks: list[dict] = []
    reasons_chunks: list[dict] = []
    for filter_ in _case_filter_tiers(info, screening):
        facts_chunks = provider._retrieve(settings.KB_CASE_ID, facts_query, filter_, _CASE_CHUNK_FETCH)
        reasons_chunks = provider._retrieve(
            settings.KB_CASE_ID, reasons_query, filter_, _CASE_CHUNK_FETCH
        )
        if facts_chunks or reasons_chunks:
            break

    ranked_case_ids = _rrf([_case_ranking(facts_chunks), _case_ranking(reasons_chunks)])[:_CASE_TOP_N]
    if not ranked_case_ids:
        return []

    # 同一案可能兩路都命中、也可能同一路命中多個 chunk;取分數最高的那一片供候選文本與摘要
    best_chunk: dict[str, dict] = {}
    for chunk in facts_chunks + reasons_chunks:
        case_id = (chunk.get("metadata") or {}).get("case_id")
        if not case_id:
            continue
        current = best_chunk.get(case_id)
        if current is None or chunk.get("score", 0) > current.get("score", 0):
            best_chunk[case_id] = chunk

    items = provider._lookup_past_decisions(ranked_case_ids)

    candidates: list[tuple[str, str]] = []
    for case_id in ranked_case_ids:
        item = items.get(case_id, {})
        header = (
            f"{item.get('year', '')}年{item.get('case_type', '')}"
            f"{item.get('case_subtype', '')}{item.get('result', '')}{item.get('appeal_article', '')}"
        )
        chunk_text = _clip(best_chunk.get(case_id, {}).get("content", {}).get("text", ""), 400)
        candidates.append((case_id, f"{header} {chunk_text}".strip()))

    query_context = f"案由：{info.case_type}；爭點：{'、'.join(info.issues)}"
    ranked_ids = rerank_ids(
        provider,
        "從候選的歷史訴願決定書中，依與本案案情的相似度排序",
        query_context,
        candidates,
        _CASE_TOP_N,
    )

    cases: list[SimilarCase] = []
    for case_id in ranked_ids:
        item = items.get(case_id)
        note = (
            "向量檢索命中（RRF 合流）＋Sonnet 重排"
            if item
            else "向量檢索命中（RRF 合流）＋Sonnet 重排，DynamoDB 未查得案件資料"
        )
        item = item or {"case_no": case_id}
        raw_law_ids = item.get("law_ids") or []
        law_ids = [int(x) for x in raw_law_ids if str(x).lstrip("-").isdigit()]
        cases.append(
            SimilarCase(
                case_no=item.get("case_no", ""),
                year=item.get("year", ""),
                case_type=item.get("case_type", ""),
                appeal_article=item.get("appeal_article", ""),
                issue=item.get("issue", ""),
                result=item.get("result", ""),
                summary=case_summary(best_chunk.get(case_id, {}).get("content", {}).get("text", "")),
                similarity_note=note,
                source_key=viewable_source_key(item),
                source_url=external_source_url(item),
                law_ids=law_ids,
            )
        )
    return cases


# ---------- F2:法規推薦(候選來自 F3 案例的 law_id,重排取 3) ----------


def recommend_laws(
    provider, info: CaseInfo, candidate_law_ids: Optional[list[int]]
) -> list[LawRef]:
    query = _retrieval_query(info)
    if candidate_law_ids:
        values = [str(lid) for lid in candidate_law_ids[:_LAW_CANDIDATE_CAP]]
        filter_ = {"in": {"key": "law_id", "value": values}}
        relevance_prefix = ""
    else:
        # 案例全無 law_ids、或 F3 零命中:退回全庫檢索,承辦人須看得出這筆推薦不是案例帶出來的
        filter_ = None
        relevance_prefix = "（無案例法規可依，改採全庫檢索）"

    retrieved = provider._retrieve(settings.KB_LAW_ID, query, filter_, _LAW_RETRIEVE_N)

    law_ids: list[int] = []
    seen: set[int] = set()
    for r in retrieved:
        lid = _law_id_of(r)
        if lid is not None and lid not in seen:
            seen.add(lid)
            law_ids.append(lid)

    items = provider._laws_by_id(law_ids) if law_ids else {}
    if retrieved and not items:
        # 全數查無不可回空清單:那與「本案查無相關法條」同形,承辦人分不出資料層斷了
        raise RuntimeError(
            f"KB-LAW 檢索命中 {len(retrieved)} 筆，"
            f"但 DynamoDB {settings.DDB_LAW_TABLE} 一筆 law_id 都查不到"
        )

    candidates: list[tuple[str, str]] = []
    for lid in law_ids:
        item = items.get(lid)
        if item is None:
            continue  # 查無者沒有法名與條號,湊不出可引用的鍵
        law_name = item.get("law_name", "")
        article_no = item.get("article_no", "")
        body = _clip(item.get("text"), 300)
        candidates.append((str(lid), f"{law_name}第{article_no}條 {body}".strip()))

    query_context = f"案由：{info.case_type}；爭點：{'、'.join(info.issues)}"
    ranked_ids = rerank_ids(
        provider, "從候選法規中挑出與本案最相關的法條", query_context, candidates, _LAW_TOP_N
    )

    laws: list[LawRef] = []
    for lid_str in ranked_ids:
        item = items.get(int(lid_str))
        if item is None:
            continue
        laws.append(
            LawRef(
                law_name=item.get("law_name", ""),
                article_no=item.get("article_no", ""),
                text=item.get("text") or "",
                # 一律來自 DynamoDB,禁止由 LLM 生成修正日期
                amend_date=item.get(settings.DDB_LAW_DATE_FIELD) or "未收錄",
                source_key=None,
                source_url=item.get("source_url") or None,
                relevance=f"{relevance_prefix}向量檢索命中（KB-LAW）＋Sonnet 重排",
            )
        )

    _append_cited_articles(provider, info, laws)
    return laws


_CITED_ARTICLE_RELEVANCE = "原處分書／訴願書明文引用"


def _append_cited_articles(provider, info: CaseInfo, laws: list[LawRef]) -> None:
    """F1 從原處分書／訴願書抽到的引用條號是 100% 準確的來源(AWS_PLAN.md「F2 精查 + 語意
    兩條路合併去重」的既定設計),附加而非取代重排結果——F4 的 cited_laws 後置過濾只認這份
    清單,漏掉就等於草稿引不到原處分依據的條文。依 F1 原順序附加,與重排 top 3 重複的鍵跳過;
    DynamoDB 精查不到的鍵誠實忽略,不報錯也不生空殼。原地修改 laws,就地附加於尾端。"""
    cited_keys = _valid_cited_articles(info.cited_articles)
    if not cited_keys:
        return
    existing_keys = {f"{law.law_name}#{law.article_no}" for law in laws}
    items = provider._lookup_law_articles(cited_keys)
    for key in cited_keys:
        if key in existing_keys:
            continue
        item = items.get(key)
        if item is None:  # 查無就忽略,不報錯、不生空殼
            continue
        law_name, _, article_no = key.partition("#")
        laws.append(
            LawRef(
                law_name=item.get("law_name", law_name),
                article_no=item.get("article_no", article_no),
                text=item.get("text") or "",
                amend_date=item.get(settings.DDB_LAW_DATE_FIELD) or "未收錄",
                source_key=None,
                source_url=item.get("source_url") or None,
                relevance=_CITED_ARTICLE_RELEVANCE,
            )
        )
        existing_keys.add(key)


# ---------- F2+:參考見解(三類各自 KB 檢索、文件聚合、重排取 3) ----------


def _batch_get_by_ref_id(provider, table_name: str, ref_ids: list[str]) -> dict[str, dict]:
    if not ref_ids:
        return {}
    resp = provider._ddb.batch_get_item(
        RequestItems={table_name: {"Keys": [{"ref_id": rid} for rid in ref_ids]}}
    )
    # 被限流而沒查成的鍵不能當成查無:那會讓資料層被限流長得像「這份文件沒收錄」
    unprocessed = (resp.get("UnprocessedKeys") or {}).get(table_name, {}).get("Keys", [])
    if unprocessed:
        raise RuntimeError(
            f"DynamoDB {table_name} 有 {len(unprocessed)} 個 ref_id 未處理(多為限流)，不視為查無"
        )
    return {item["ref_id"]: item for item in resp.get("Responses", {}).get(table_name, [])}


def find_references(provider, info: CaseInfo) -> list[ReferenceRef]:
    # 全文已切片進三個 KB,查詢端用案由+全部爭點+理由(截 900 字)對三個 KB 各查一次,不加 filter
    joined_issues = "、".join(info.issues)
    joined_reasons = "".join(info.appeal_reasons)
    query = _clip(f"{info.case_type} {joined_issues} {joined_reasons}", _QUERY_MAX_CHARS)

    refs: list[ReferenceRef] = []
    for doc_kind in _REF_DOC_KINDS:
        kb_id = getattr(settings, _KB_ID_BY_DOC_KIND[doc_kind])
        if not kb_id:
            # 部署環境可能還沒設該類 KB,不 raise——另外兩類仍要跑完
            logger.warning("F2+ %s 的 KB id 未設定，跳過此類", doc_kind)
            continue

        retrieved = provider._retrieve(kb_id, query, None, _REF_FETCH)

        # 依 ref_id 聚合:同一份文件的多片命中分數加總,代表這份文件整體的相關度
        agg: dict[str, list[tuple[float, str]]] = {}
        for r in retrieved:
            ref_id = (r.get("metadata") or {}).get("ref_id")
            if not ref_id:
                continue
            score = r.get("score", 0.0)
            # content.text 鍵存在但值為 None 時 .get(key, default) 不會退回 default,須明確 or ""
            chunk_text = r.get("content", {}).get("text") or ""
            agg.setdefault(ref_id, []).append((score, chunk_text))

        ranked_by_score = sorted(agg, key=lambda rid: sum(s for s, _ in agg[rid]), reverse=True)
        top_ref_ids = ranked_by_score[:_REF_AGG_TOP]

        def _top_chunks_text(ref_id: str, limit: int) -> str:
            chunks = sorted(agg[ref_id], key=lambda c: c[0], reverse=True)[:2]
            return _clip(" ".join(t for _, t in chunks), limit)

        candidates = [(ref_id, f"{ref_id} {_top_chunks_text(ref_id, 600)}".strip()) for ref_id in top_ref_ids]
        query_context = f"文件類別：{doc_kind}；案由：{info.case_type}；爭點：{joined_issues}"
        ranked_ids = rerank_ids(
            provider, f"從候選{doc_kind}中挑出與本案最相關者", query_context, candidates, _REF_TOP_N
        )

        table_name = getattr(settings, _DDB_TABLE_BY_DOC_KIND[doc_kind])
        items = _batch_get_by_ref_id(provider, table_name, ranked_ids)
        for ref_id in ranked_ids:
            item = items.get(ref_id)
            text_ = _top_chunks_text(ref_id, 800)
            if item is None:
                refs.append(
                    ReferenceRef(
                        doc_kind=doc_kind,
                        name=ref_id,
                        issuer="",
                        issued_date="未收錄",
                        topic="",
                        text=text_,
                        source_key=None,
                        source_url=None,
                        relevance="向量檢索命中＋Sonnet 重排，DynamoDB 未查得原文資料",
                    )
                )
                continue
            refs.append(
                ReferenceRef(
                    doc_kind=item.get("doc_kind") or doc_kind,
                    name=ref_id,
                    issuer=item.get("issuer") or "",
                    issued_date=item.get("issued_date") or "未收錄",
                    topic=item.get("topic") or "",
                    text=text_,
                    source_key=item.get("s3_key") or None,
                    source_url=item.get("source_url") or None,
                    relevance="向量檢索命中＋Sonnet 重排",
                )
            )
    return refs
