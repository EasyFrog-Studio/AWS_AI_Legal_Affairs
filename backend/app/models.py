"""契約 Pydantic models(見 DECISIONS.md「Backend API 契約」節,逐字照做)。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class CaseInfo(BaseModel):
    appellant: str
    agency: str
    disposition_date: str
    disposition_no: str
    disposition_summary: str
    appeal_reasons: list[str] = []
    case_type: str
    issues: list[str] = []
    cited_articles: list[str] = []  # 如 "廢棄物清理法#46"


class ScreeningResult(BaseModel):
    passed: bool
    matched_clause: Optional[str] = None  # 如 "77條第2款"
    reasoning: str


class LawRef(BaseModel):
    law_name: str
    article_no: str
    text: str
    amend_date: str
    source_key: Optional[str] = None
    relevance: str


class SimilarCase(BaseModel):
    case_no: str
    year: str
    case_type: str
    appeal_article: str
    issue: str
    result: str
    summary: str
    similarity_note: str
    source_key: Optional[str] = None


class DraftResult(BaseModel):
    draft_type: Literal["不受理", "駁回", "原處分撤銷"]
    fact: str
    reason: str
    main_text: str
    cited_laws: list[str] = []


class DraftPatch(BaseModel):
    """PATCH /api/cases/{id}/draft 的 body。"""

    fact: str
    reason: str
    main_text: str


Stage = Literal["f1", "screening", "f2", "f3", "f4", "done"]
Status = Literal["processing", "done", "error"]
Track = Literal["admissible", "inadmissible"]
Source = Literal["pdf", "text"]


class Case(BaseModel):
    """store 與前端共用的完整案件物件 schema。"""

    case_id: str
    created_at: str
    title: str
    status: Status = "processing"
    current_stage: Stage = "f1"
    track: Optional[Track] = None
    source: Source
    input_text: str
    f1: Optional[CaseInfo] = None
    screening: Optional[ScreeningResult] = None
    f2: Optional[list[LawRef]] = None
    f3: Optional[list[SimilarCase]] = None
    f4: Optional[DraftResult] = None
    error: Optional[str] = None


class CaseSummary(BaseModel):
    case_id: str
    created_at: str
    title: str
    status: Status
    track: Optional[Track] = None
    current_stage: Stage
    case_type: Optional[str] = None
