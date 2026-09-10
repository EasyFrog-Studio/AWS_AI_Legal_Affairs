"""AWS Bedrock AIProvider:converse+toolConfig 強制 JSON、KB retrieve+metadata filter、DynamoDB 精查。
amend_date 一律來自 DynamoDB/metadata,查不到填「未收錄」,絕不由 LLM 生成。
"""
import json
import re
from pathlib import Path
from typing import Optional

from app.config import settings
from app.models import (
    DRAFT_TYPES,
    CaseInfo,
    DraftResult,
    LawRef,
    ScreeningResult,
    SimilarCase,
    StandingAssessment,
    parse_clause,
)
from app.providers.base import AIProvider

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_DDB_BATCH_LIMIT = 100  # DynamoDB BatchGetItem 單次上限
_ARTICLE_NO_RE = re.compile(r"^\d+(-\d+)?$")  # 如 "27"、"27-1";F1 抽不到條號時填「未載明」,不是有效條號
_MAX_QUERY_ISSUE_CHARS = 40  # F2 查詢只取首個爭點的前 N 字,避免多爭點串成長句把語意重心稀釋掉
_TOP_K = 3  # F2 法規 / F3 案例各自呈現的筆數上限
# 一份決定書切成多個欄位段落 chunk,取 _TOP_K 個 chunk 可能全落在同一案號,故案例先多撈再去重
_CASE_CHUNK_FETCH = _TOP_K * 5


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _clause_to_appeal_article(clause: Optional[str]) -> Optional[str]:
    """"77條第2款" -> "77(2)"(對應 case_chunks metadata.appeal_article 格式)。"""
    parsed = parse_clause(clause)
    return f"{parsed[0]}({parsed[1]})" if parsed else None


def _valid_cited_articles(cited_articles: list[str]) -> list[str]:
    """過濾 F1 產出的假條號(如 "廢棄物清理法#未載明")——條號欄位查無明確依據時 prompt 要求填
    「未載明」,這種鍵送進精查必然查無,只會產出 text="" 的空殼卻仍被算進可引用清單。"""
    return [a for a in cited_articles if _ARTICLE_NO_RE.match(a.partition("#")[2])]


_CHUNK_HEADER_RE = re.compile(r"^【[^】]*】[^\n]*\n")
_SUMMARY_LIMIT = 200


def case_summary(text: str) -> str:
    """chunk 原文 -> 畫面上讀得懂的案例摘要:去掉【…】前綴、切在句尾。"""
    body = _CHUNK_HEADER_RE.sub("", text or "", count=1).strip()
    if len(body) <= _SUMMARY_LIMIT:
        return body
    window = body[:_SUMMARY_LIMIT]
    cut = window.rfind("。")
    # 整段無句號(表格、條列殘段)時仍截一段回去,空摘要會讓該筆案例看起來沒內容
    return window[: cut + 1] if cut > 0 else window


def _retrieval_query(info: CaseInfo) -> str:
    """F2/F3 共用的檢索查詢字串:只取首個爭點(截斷),不把全部爭點串成長句。issues 全句串接
    會把語意重心稀釋掉,案由本身(case_type)與最主要的爭點才是決定該撈哪部法規/哪些案例的關鍵訊號。"""
    primary_issue = info.issues[0][:_MAX_QUERY_ISSUE_CHARS] if info.issues else ""
    return f"{info.case_type} {primary_issue}".strip()


class AWSProvider(AIProvider):
    def __init__(
        self,
        bedrock_runtime=None,
        bedrock_agent_runtime=None,
        dynamodb_resource=None,
    ) -> None:
        import boto3

        region = settings.AWS_REGION
        self._brt = bedrock_runtime or boto3.client("bedrock-runtime", region_name=region)
        self._bart = bedrock_agent_runtime or boto3.client(
            "bedrock-agent-runtime", region_name=region
        )
        self._ddb = dynamodb_resource or boto3.resource("dynamodb", region_name=region)

    # ---------- bedrock-runtime converse:toolConfig 強制 JSON schema ----------
    def _converse_json(
        self,
        system_prompt: str,
        user_text: str,
        tool_name: str,
        schema: dict,
        temperature: Optional[float] = None,
    ) -> dict:
        kwargs: dict = {
            "modelId": settings.BEDROCK_MODEL_ID,
            "system": [{"text": system_prompt}],
            "messages": [{"role": "user", "content": [{"text": user_text}]}],
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": tool_name,
                            "description": f"輸出 {tool_name} 結構化結果",
                            "inputSchema": {"json": schema},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": tool_name}},
            },
        }
        if temperature is not None:
            kwargs["inferenceConfig"] = {"temperature": temperature}
        resp = self._brt.converse(**kwargs)
        content = resp["output"]["message"]["content"]
        for block in content:
            if "toolUse" in block:
                return block["toolUse"]["input"]
        raise RuntimeError(f"Bedrock converse 未回傳 toolUse 結果: {resp}")

    def extract_case_info(self, text: str) -> CaseInfo:
        schema = {
            "type": "object",
            "properties": {
                "appellant": {"type": "string"},
                "agency": {"type": "string"},
                "disposition_date": {"type": "string"},
                "disposition_no": {"type": "string"},
                "disposition_summary": {"type": "string"},
                "appeal_reasons": {"type": "array", "items": {"type": "string"}},
                "case_type": {"type": "string"},
                "issues": {"type": "array", "items": {"type": "string"}},
                "cited_articles": {"type": "array", "items": {"type": "string"}},
                "receipt_date": {"type": "string"},
                "service_date": {"type": "string"},
                "service_method": {"type": "string"},
                "disposition_fine": {"type": "string"},
                "disposition_notice_clause": {"type": "string"},
                "disposition_recipient": {"type": "string"},
            },
            "required": [
                "appellant",
                "agency",
                "disposition_date",
                "disposition_no",
                "disposition_summary",
                "case_type",
            ],
        }
        data = self._converse_json(_load_prompt("f1_extract.txt"), text, "extract_case_info", schema)
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
        data = self._converse_json(_load_prompt("screening.txt"), user_text, "screen_admissibility", schema)
        return ScreeningResult(**data)

    def assess_standing(self, info: CaseInfo, text: str) -> StandingAssessment:
        """保護規範理論的判斷是全流程唯一需要 LLM 做價值判斷的節點,同一卷證跑兩次必須得到
        同一結果(見實作計畫 §Ticket 6 約束3),故固定 temperature=0,不受其他呼叫的預設值影響。"""
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
        data = self._converse_json(
            _load_prompt("standing.txt"), user_text, "assess_standing", schema, temperature=0
        )
        return StandingAssessment(
            referenced_norm=data.get("referenced_norm") or "", has_standing=data.get("has_standing")
        )

    # ---------- bedrock-agent-runtime retrieve ----------
    def _retrieve(
        self, kb_id: str, query: str, filter_: Optional[dict], num_results: int = _TOP_K
    ) -> list[dict]:
        vector_search_config: dict = {"numberOfResults": num_results}
        if filter_:
            vector_search_config["filter"] = filter_
        resp = self._bart.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": vector_search_config},
        )
        return resp.get("retrievalResults", [])

    # ---------- DynamoDB 精查(法條全文/修正日期) ----------
    def _batch_get_law_articles(self, keys: list[str]) -> dict[str, dict]:
        result: dict[str, dict] = {}
        table_name = settings.DDB_LAW_TABLE
        for i in range(0, len(keys), _DDB_BATCH_LIMIT):
            chunk = keys[i : i + _DDB_BATCH_LIMIT]
            resp = self._ddb.batch_get_item(
                RequestItems={table_name: {"Keys": [{"law_article": k} for k in chunk]}}
            )
            for item in resp.get("Responses", {}).get(table_name, []):
                result[item["law_article"]] = item
        return result

    def get_law_articles(self, keys: list[str]) -> list[LawRef]:
        """依「法規名稱#條號」精查全文與修正日期;條號是精確鍵,不走向量檢索。"""
        items = self._batch_get_law_articles(keys)
        refs: list[LawRef] = []
        for key in keys:
            item = items.get(key)
            law_name, _, article_no = key.partition("#")
            refs.append(
                LawRef(
                    law_name=item.get("law_name", law_name) if item else law_name,
                    article_no=item.get("article_no", article_no) if item else article_no,
                    text=item.get("text", "") if item else "",
                    # 一律來自 DynamoDB,查無資料填死值,禁止由 LLM 生成修正日期
                    amend_date=(item.get("amend_date") or "未收錄") if item else "未收錄",
                    source_key=item.get("source_key") if item else None,
                    relevance="條號精查(DynamoDB)" if item else "條號精查,DynamoDB 未查得資料",
                )
            )
        return refs

    def recommend_laws(self, info: CaseInfo) -> list[LawRef]:
        filter_ = {"notEquals": {"key": "law_type", "value": "普通法"}}
        retrieved = self._retrieve(settings.KB_LAW_ID, _retrieval_query(info), filter_)

        law_refs: dict[str, LawRef] = {}
        for r in retrieved:
            metadata = r.get("metadata", {})
            law_name = metadata.get("law_name", "")
            article_no = metadata.get("article_no", "")
            key = f"{law_name}#{article_no}"
            law_refs[key] = LawRef(
                law_name=law_name,
                article_no=article_no,
                text=r.get("content", {}).get("text", ""),
                amend_date=metadata.get("amend_date") or "未收錄",  # 一律來自 KB metadata,LLM 不生成
                source_key=metadata.get("source_file"),
                relevance="向量檢索命中(KB-LAW)",
            )

        # F1 cited_articles 走 DynamoDB 精查,補齊 KB 檢索未涵蓋的引用法條;先濾掉「未載明」等假條號
        missing_keys = [a for a in _valid_cited_articles(info.cited_articles) if a not in law_refs]
        # 以請求的 key 落位,不用回傳值重組:DB 的 law_name/article_no 與 key 不一致時,
        # 重組出的 key 會撞掉檢索結果
        for key, ref in zip(missing_keys, self.get_law_articles(missing_keys)):
            law_refs[key] = ref
        return list(law_refs.values())

    def find_similar_cases(
        self, info: CaseInfo, screening: ScreeningResult, text: str
    ) -> list[SimilarCase]:
        query = _retrieval_query(info)

        if screening.passed:
            filter_ = {"equals": {"key": "case_type", "value": info.case_type}}
        else:
            clauses = [
                {"equals": {"key": "case_type", "value": info.case_type}},
                {"equals": {"key": "result", "value": "不受理"}},
            ]
            appeal_article = _clause_to_appeal_article(screening.matched_clause)
            if appeal_article:
                clauses.append({"equals": {"key": "appeal_article", "value": appeal_article}})
            filter_ = {"andAll": clauses}

        retrieved = self._retrieve(settings.KB_CASE_ID, query, filter_, _CASE_CHUNK_FETCH)
        if not retrieved:
            # 無結果則放寬 filter(僅保留 case_type)重查一次
            relaxed_filter = {"equals": {"key": "case_type", "value": info.case_type}}
            retrieved = self._retrieve(
                settings.KB_CASE_ID, query, relaxed_filter, _CASE_CHUNK_FETCH
            )
        if not retrieved:
            # F1 的 case_type 字面可能與 KB metadata 不一致(如「廢棄物清理」vs「廢棄物清理法」),
            # 最後退為純語意檢索(不受理案件仍保留 result filter)
            last_filter = None if screening.passed else {"equals": {"key": "result", "value": "不受理"}}
            retrieved = self._retrieve(settings.KB_CASE_ID, query, last_filter, _CASE_CHUNK_FETCH)

        cases: dict[str, SimilarCase] = {}
        for r in retrieved:
            metadata = r.get("metadata", {})
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
                summary=case_summary(r.get("content", {}).get("text", "")),
                similarity_note="向量檢索命中(KB-CASE)",
                source_key=metadata.get("source_file"),
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
                "draft_type": {"type": "string", "enum": list(DRAFT_TYPES)},
                "fact": {"type": "string"},
                "reason": {"type": "string"},
                "main_text": {"type": "string"},
                "cited_laws": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["draft_type", "fact", "reason", "main_text"],
        }
        allowed_laws = [f"{l.law_name}#{l.article_no}" for l in laws]
        user_text = (
            f"【案件資訊】\n{info.model_dump_json(indent=2)}\n\n"
            f"【審查結果】\n{screening.model_dump_json(indent=2)}\n\n"
            f"【可引用法規清單(僅能引用此清單內的法條,不得自創法條)】\n"
            f"{json.dumps(allowed_laws, ensure_ascii=False)}\n\n"
            f"【相似案例】\n{json.dumps([c.model_dump() for c in cases], ensure_ascii=False)}"
        )
        data = self._converse_json(_load_prompt("f4_draft.txt"), user_text, "generate_draft", schema)
        # 防禦性過濾:即使 LLM 違反指示,也強制 cited_laws 只能是提供清單的子集
        data["cited_laws"] = [c for c in data.get("cited_laws", []) if c in allowed_laws]
        return DraftResult(**data)
