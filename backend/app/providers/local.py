"""地端 AIProvider:ollama /api/chat(format=JSON schema)+ /api/embed、pgvector 檢索、law_articles 精查。
amend_date 一律來自 metadata/law_articles,查不到填「未收錄」,絕不由 LLM 生成。
"""
import json
from typing import Optional

from app.config import settings
from app.models import (
    CaseInfo,
    DraftResult,
    LawRef,
    ReferenceRef,
    ScreeningResult,
    SimilarCase,
    StandingAssessment,
    draft_types_for,
)
from app.providers.aws import (
    _CASE_CHUNK_FETCH,
    _REF_CHUNK_FETCH,
    _REF_DOC_KINDS,
    _TOP_K,
    _cap_law_refs,
    _clause_to_appeal_article,
    _load_prompt,
    _retrieval_query,
    _valid_cited_articles,
    build_references,
    external_source_url,
    viewable_source_key,
    case_summary,
)
from app.providers.base import AIProvider
from app.providers.schemas import case_info_json_schema


_MAX_OUTPUT_TOKENS = 2048  # 語料最長 f4 為 879 字,留兩倍餘裕;調小會攔腰砍掉正常草稿


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vec) + "]"


class LocalProvider(AIProvider):
    def __init__(self, http_client=None, connect=None) -> None:
        self._llm_model = settings.LOCAL_LLM_MODEL
        self._embed_model = settings.LOCAL_EMBED_MODEL

        if http_client is None:
            import httpx

            http_client = httpx.Client(
                base_url=settings.LOCAL_LLM_BASE_URL, timeout=settings.LOCAL_LLM_TIMEOUT
            )
        self._http = http_client

        if connect is None:
            if not settings.POSTGRES_URL:
                raise RuntimeError("POSTGRES_URL 未設定，local 模式需要 Postgres 連線字串")

            def _default_connect():
                import psycopg

                # autocommit:唯讀查詢不留 idle-in-transaction,失敗的陳述式也不會
                # 讓重用中的連線卡在 aborted 交易狀態
                return psycopg.connect(settings.POSTGRES_URL, autocommit=True)

            connect = _default_connect
        self._connect = connect
        self._conn = None  # 延遲建立,之後跨查詢重用(比照 PostgresStore)

    # ---------- ollama /api/chat:format=JSON schema 強制結構化輸出 ----------
    def _chat_json(self, system_prompt: str, user_text: str, schema: dict) -> dict:
        resp = self._http.post(
            "/api/chat",
            json={
                "model": self._llm_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                "stream": False,
                "format": schema,
                # num_ctx:ollama 預設 4096,F4 輸入(案件+法規+案例 JSON)會超過而被靜默截斷。
                # num_predict/repeat_penalty 是失控生成的煞車:模型曾在 schema 約束下無限重複,
                # 拖到 client 300 秒逾時而伺服器端還在算。2048 取自語料統計(最長 f4 為 879 字)
                "options": {
                    "temperature": 0,
                    "num_ctx": 16384,
                    "num_predict": _MAX_OUTPUT_TOKENS,
                    "repeat_penalty": 1.1,
                },
            },
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("done_reason") == "length":
            raise RuntimeError(
                f"模型輸出觸及生成長度上限（{_MAX_OUTPUT_TOKENS} token）而被截斷，結果不完整"
            )
        return json.loads(body["message"]["content"])

    # ---------- ollama /api/embed ----------
    def _embed(self, text: str) -> list[float]:
        resp = self._http.post(
            "/api/embed",
            json={"model": self._embed_model, "input": [text]},
        )
        resp.raise_for_status()
        return resp.json()["embeddings"][0]

    # ---------- pgvector / Postgres 查詢 ----------
    def _execute(self, sql: str, params) -> list[tuple]:
        if self._conn is None:
            self._conn = self._connect()
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def extract_case_info(self, text: str) -> CaseInfo:
        schema = case_info_json_schema()
        data = self._chat_json(_load_prompt("f1_extract.txt"), text, schema)
        return CaseInfo(**data)

    def screen_admissibility(self, info: CaseInfo, text: str) -> ScreeningResult:
        schema = {
            "type": "object",
            "properties": {
                "passed": {"type": "boolean"},
                "matched_clause": {"type": ["string", "null"]},
                "reasoning": {"type": "string"},
            },
            "required": ["passed", "reasoning"],
        }
        user_text = f"【案件資訊】\n{info.model_dump_json(indent=2)}\n\n【訴願書原文】\n{text}"
        data = self._chat_json(_load_prompt("screening.txt"), user_text, schema)
        return ScreeningResult(**data)

    def assess_standing(self, info: CaseInfo, text: str) -> StandingAssessment:
        """_chat_json 的 options 已固定 temperature=0(見上),同一卷證跑兩次得到同一結果
        不需要在這裡額外處理。"""
        schema = {
            "type": "object",
            "properties": {
                "referenced_norm": {"type": "string"},
                "has_standing": {"type": ["boolean", "null"]},
                "reasoning": {"type": "string"},
            },
            "required": ["referenced_norm", "has_standing", "reasoning"],
        }
        user_text = f"【案件資訊】\n{info.model_dump_json(indent=2)}\n\n【訴願書原文】\n{text}"
        data = self._chat_json(_load_prompt("standing.txt"), user_text, schema)
        return StandingAssessment(
            referenced_norm=data.get("referenced_norm") or "", has_standing=data.get("has_standing")
        )

    def get_law_articles(self, keys: list[str]) -> list[LawRef]:
        """依「法規名稱#條號」精查全文與修正日期;條號是精確鍵,不走向量檢索。"""
        if not keys:
            return []
        rows = self._execute(
            "SELECT law_article, text, metadata FROM law_articles WHERE law_article = ANY(%s)",
            (keys,),
        )
        found = {law_article: (text, metadata or {}) for law_article, text, metadata in rows}
        refs: list[LawRef] = []
        for key in keys:
            law_name, _, article_no = key.partition("#")
            hit = found.get(key)
            text, metadata = hit if hit else ("", {})
            refs.append(
                LawRef(
                    law_name=metadata.get("law_name", law_name),
                    article_no=metadata.get("article_no", article_no),
                    text=text or "",
                    # 一律來自 law_articles,查無資料填死值,禁止由 LLM 生成修正日期
                    amend_date=(metadata.get("amend_date") or "未收錄") if hit else "未收錄",
                    source_key=metadata.get("source_key"),
                    relevance="條號精查（law_articles）" if hit else "條號精查，law_articles 未查得資料",
                )
            )
        return refs

    # ---------- pgvector law_chunks 檢索 + law_articles 精查(對應 AWSProvider.recommend_laws) ----------
    def recommend_laws(
        self, info: CaseInfo, candidate_law_ids: Optional[list[int]] = None
    ) -> list[LawRef]:
        vec_lit = _vector_literal(self._embed(_retrieval_query(info)))
        rows = self._execute(
            "SELECT id, text, metadata, 1 - (embedding <=> %s::vector) AS score "
            "FROM law_chunks "
            # 函釋/釋字/裁判入庫但不供 F2:它們沒有條號,湊不出 LawRef 的「法名#條號」鍵
            "WHERE metadata->>'doc_kind' = '法規' "
            "AND metadata->>'law_type' IS DISTINCT FROM '普通法' "
            "ORDER BY embedding <=> %s::vector LIMIT %s",
            (vec_lit, vec_lit, _TOP_K),
        )

        law_refs: dict[str, LawRef] = {}
        for _id, text, metadata, _score in rows:
            law_name = metadata.get("law_name", "")
            article_no = metadata.get("article_no", "")
            key = f"{law_name}#{article_no}"
            law_refs[key] = LawRef(
                law_name=law_name,
                article_no=article_no,
                text=text,
                amend_date=metadata.get("amend_date") or "未收錄",  # 一律來自檢索 metadata,LLM 不生成
                source_key=viewable_source_key(metadata),
                relevance="向量檢索命中（pgvector law_chunks）",
            )

        # F1 cited_articles 走 law_articles 精查,補齊向量檢索未涵蓋的引用法條;先濾掉「未載明」等假條號
        cited_keys = _valid_cited_articles(info.cited_articles)
        missing_keys = [a for a in cited_keys if a not in law_refs]
        # 以請求的 key 落位,不用回傳值重組:metadata 的 law_name/article_no 與 key 不一致時,
        # 重組出的 key 會撞掉檢索結果
        for key, ref in zip(missing_keys, self.get_law_articles(missing_keys)):
            law_refs[key] = ref
        return _cap_law_refs(law_refs, cited_keys)


    def find_references(self, info: CaseInfo) -> list[ReferenceRef]:
        vec_lit = _vector_literal(self._embed(_retrieval_query(info)))
        refs: list[ReferenceRef] = []
        for doc_kind in _REF_DOC_KINDS:
            rows = self._execute(
                "SELECT text, metadata FROM law_chunks "
                # 對映 KB-LAW 的 equals doc_kind:三類各自取前 _REF_TOP_K,互不排擠
                "WHERE metadata->>'doc_kind' = %s "
                "ORDER BY embedding <=> %s::vector LIMIT %s",
                (doc_kind, vec_lit, _REF_CHUNK_FETCH),
            )
            refs.extend(
                build_references(
                    [
                        ((metadata or {}).get("law_name", ""), text, metadata or {})
                        for text, metadata in rows
                    ],
                    "向量檢索命中（pgvector law_chunks，非法規）",
                )
            )
        return refs

    def _search_cases(
        self, vec_lit: str, where_sql, where_params, num_results: int = _CASE_CHUNK_FETCH
    ) -> list[tuple]:
        sql = "SELECT id, text, metadata, 1 - (embedding <=> %s::vector) AS score FROM case_chunks"
        params: list = [vec_lit]
        if where_sql:
            sql += f" WHERE {where_sql}"
            params.extend(where_params)
        sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
        params.extend([vec_lit, num_results])
        return self._execute(sql, tuple(params))

    def find_similar_cases(
        self, info: CaseInfo, screening: ScreeningResult, text: str
    ) -> list[SimilarCase]:
        vec_lit = _vector_literal(self._embed(_retrieval_query(info)))

        if screening.passed:
            where_sql, where_params = "metadata->>'case_type' = %s", [info.case_type]
        else:
            clauses = ["metadata->>'case_type' = %s", "metadata->>'result' = %s"]
            where_params = [info.case_type, "不受理"]
            appeal_article = _clause_to_appeal_article(screening.matched_clause)
            if appeal_article:
                clauses.append("metadata->>'appeal_article' = %s")
                where_params.append(appeal_article)
            where_sql = " AND ".join(clauses)

        rows = self._search_cases(vec_lit, where_sql, where_params)
        if not rows:
            # 無結果則放寬 filter(僅保留 case_type)重查一次
            rows = self._search_cases(vec_lit, "metadata->>'case_type' = %s", [info.case_type])
        if not rows:
            # F1 的 case_type 字面可能與檢索 metadata 不一致(如「廢棄物清理」vs「廢棄物清理法」),
            # 最後退為純語意檢索(不受理案件仍保留 result filter)
            if screening.passed:
                rows = self._search_cases(vec_lit, None, [])
            else:
                rows = self._search_cases(vec_lit, "metadata->>'result' = %s", ["不受理"])

        cases: dict[str, SimilarCase] = {}
        for _id, text_, metadata, _score in rows:
            case_no = metadata.get("case_no", "")
            if not case_no:
                continue  # 缺 case_no 的結果不可辨識,跳過以免以空 key 相互覆蓋
            cases[case_no] = SimilarCase(
                case_no=case_no,
                year=metadata.get("year", ""),
                case_type=metadata.get("case_type", ""),
                appeal_article=metadata.get("appeal_article", ""),
                issue=metadata.get("issue", ""),
                result=metadata.get("result", ""),
                summary=case_summary(text_),
                similarity_note="向量檢索命中（pgvector case_chunks）",
                source_key=viewable_source_key(metadata),
                source_url=external_source_url(metadata),
            )
        return list(cases.values())[:_TOP_K]

    def generate_draft(
        self,
        info: CaseInfo,
        screening: ScreeningResult,
        laws: list[LawRef],
        cases: list[SimilarCase],
    ) -> DraftResult:
        schema = {
            "type": "object",
            "properties": {
                "draft_type": {"type": "string", "enum": list(draft_types_for(screening.passed))},
                "fact": {"type": "string"},
                "reason": {"type": "string"},
                "main_text": {"type": "string"},
                "cited_laws": {"type": "array", "items": {"type": "string"}},
                "gist": {"type": "string"},
            },
            "required": ["draft_type", "fact", "reason", "main_text", "gist"],
        }
        allowed_laws = [f"{l.law_name}#{l.article_no}" for l in laws]
        user_text = (
            f"【案件資訊】\n{info.model_dump_json(indent=2)}\n\n"
            f"【審查結果】\n{screening.model_dump_json(indent=2)}\n\n"
            f"【可引用法規清單(僅能引用此清單內的法條,不得自創法條)】\n"
            f"{json.dumps(allowed_laws, ensure_ascii=False)}\n\n"
            f"【相似案例】\n{json.dumps([c.model_dump() for c in cases], ensure_ascii=False)}"
        )
        data = self._chat_json(_load_prompt("f4_draft.txt"), user_text, schema)
        # 防禦性過濾:即使 LLM 違反指示,也強制 cited_laws 只能是提供清單的子集
        data["cited_laws"] = [c for c in data.get("cited_laws", []) if c in allowed_laws]
        return DraftResult(**data)
