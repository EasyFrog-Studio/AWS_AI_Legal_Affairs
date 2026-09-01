"""AWS Bedrock AIProvider:converse+toolConfig 強制 JSON、KB retrieve+metadata filter、DynamoDB 精查。
amend_date 一律來自 DynamoDB/metadata,查不到填「未收錄」,絕不由 LLM 生成。
"""
import json
import re
from pathlib import Path
from typing import Optional

from app.config import settings
from app.models import CaseInfo, DraftResult, LawRef, ScreeningResult, SimilarCase
from app.providers.base import AIProvider

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_DDB_BATCH_LIMIT = 100  # DynamoDB BatchGetItem 單次上限
_CLAUSE_RE = re.compile(r"(\d+)條第(\d+)款")


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _clause_to_appeal_article(clause: Optional[str]) -> Optional[str]:
    """"77條第2款" -> "77(2)"(對應 case_chunks metadata.appeal_article 格式)。"""
    if not clause:
        return None
    m = _CLAUSE_RE.search(clause)
    if not m:
        return None
    return f"{m.group(1)}({m.group(2)})"


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
        self, system_prompt: str, user_text: str, tool_name: str, schema: dict
    ) -> dict:
        resp = self._brt.converse(
            modelId=settings.BEDROCK_MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_text}]}],
            toolConfig={
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
        )
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

    # ---------- bedrock-agent-runtime retrieve ----------
    def _retrieve(self, kb_id: str, query: str, filter_: Optional[dict], num_results: int = 5) -> list[dict]:
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

    def recommend_laws(self, info: CaseInfo) -> list[LawRef]:
        filter_ = {"notEquals": {"key": "law_type", "value": "普通法"}}
        query = f"{info.case_type} {' '.join(info.issues)}".strip()
        retrieved = self._retrieve(settings.KB_LAW_ID, query, filter_)

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

        # F1 cited_articles 走 DynamoDB 精查,補齊 KB 檢索未涵蓋的引用法條
        missing_keys = [a for a in info.cited_articles if a not in law_refs]
        if missing_keys:
            ddb_items = self._batch_get_law_articles(missing_keys)
            for key in missing_keys:
                item = ddb_items.get(key)
                law_name, _, article_no = key.partition("#")
                if item:
                    law_refs[key] = LawRef(
                        law_name=item.get("law_name", law_name),
                        article_no=item.get("article_no", article_no),
                        text=item.get("text", ""),
                        amend_date=item.get("amend_date") or "未收錄",  # 一律來自 DynamoDB
                        source_key=item.get("source_key"),
                        relevance="F1 擷取之引用法條(DynamoDB 精查)",
                    )
                else:
                    law_refs[key] = LawRef(
                        law_name=law_name,
                        article_no=article_no,
                        text="",
                        amend_date="未收錄",  # 查無資料,禁止由 LLM 生成修正日期
                        source_key=None,
                        relevance="F1 擷取之引用法條,DynamoDB 未查得資料",
                    )
        return list(law_refs.values())

    def find_similar_cases(
        self, info: CaseInfo, screening: ScreeningResult, text: str
    ) -> list[SimilarCase]:
        query = f"{info.case_type} {' '.join(info.issues)}".strip()

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

        retrieved = self._retrieve(settings.KB_CASE_ID, query, filter_)
        if not retrieved:
            # 無結果則放寬 filter(僅保留 case_type)重查一次
            relaxed_filter = {"equals": {"key": "case_type", "value": info.case_type}}
            retrieved = self._retrieve(settings.KB_CASE_ID, query, relaxed_filter)
        if not retrieved:
            # F1 的 case_type 字面可能與 KB metadata 不一致(如「廢棄物清理」vs「廢棄物清理法」),
            # 最後退為純語意檢索(不受理案件仍保留 result filter)
            last_filter = None if screening.passed else {"equals": {"key": "result", "value": "不受理"}}
            retrieved = self._retrieve(settings.KB_CASE_ID, query, last_filter)

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
                summary=r.get("content", {}).get("text", "")[:200],
                similarity_note="向量檢索命中(KB-CASE)",
                source_key=metadata.get("source_file"),
            )
        return list(cases.values())

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
                "draft_type": {"type": "string", "enum": ["不受理", "駁回", "原處分撤銷"]},
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
