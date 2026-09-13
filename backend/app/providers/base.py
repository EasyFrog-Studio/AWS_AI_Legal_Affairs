"""AIProvider 介面,三種 provider 共用簽名。"""
from abc import ABC, abstractmethod
from typing import Optional

from app.models import (
    CaseInfo,
    DraftResult,
    LawRef,
    ReferenceRef,
    ScreeningResult,
    SimilarCase,
    StandingAssessment,
)


class AIProvider(ABC):
    @abstractmethod
    def extract_case_info(self, text: str) -> CaseInfo: ...  # F1

    @abstractmethod
    def screen_admissibility(self, info: CaseInfo, text: str) -> ScreeningResult: ...

    @abstractmethod
    def assess_standing(self, info: CaseInfo, text: str) -> StandingAssessment:
        """§77(3) 當事人適格:處分相對人與訴願人不一致時,判斷訴願人是否仍有法律上利害關係
        (訴願法§18,保護規範理論)。這是整條流程唯一需要 LLM 做價值判斷的節點——
        其餘階段(F2/F3 檢索、期間計算、必要記載檢核)都是可查證的事實或條文比對。
        回傳結構化的 StandingAssessment,不是自由論述:referenced_norm 是模型指出的具體
        保護規範(法規名稱#條號);has_standing 是 True(有利害關係)/False(無)/None(依據不足)。
        指不出 referenced_norm 時,呼叫端(procedural_checks.resolve_standing_assessment)
        一律視為無法判斷,不採用 has_standing——這裡只誠實回傳模型說了什麼,不做這層過濾。"""
        ...

    @abstractmethod
    def recommend_laws(
        self, info: CaseInfo, candidate_law_ids: Optional[list[int]] = None
    ) -> list[LawRef]:
        """F2。candidate_law_ids 非 None 時是 F3 相似案例帶出的候選 law_id(依出現次數遞減),
        aws 據此加 KB-LAW 的 in filter;為 None 或空清單時退回全庫檢索。"""
        ...

    @abstractmethod
    def find_references(self, info: CaseInfo) -> list[ReferenceRef]:
        """F2+ 參考見解:行政函釋/司法院釋字/行政法院裁判。與法規同一個檢索庫,以 doc_kind 區分。
        供承辦人論理時參考,不進 F4 的可引用清單——它們沒有條號,引用格式湊不出來。"""
        ...

    @abstractmethod
    def get_law_articles(self, keys: list[str]) -> list[LawRef]: ...  # 依「法規#條號」精查

    @abstractmethod
    def find_similar_cases(
        self, info: CaseInfo, screening: ScreeningResult, text: str
    ) -> list[SimilarCase]: ...  # F3

    @abstractmethod
    def generate_draft(
        self,
        info: CaseInfo,
        screening: ScreeningResult,
        laws: list[LawRef],
        cases: list[SimilarCase],
    ) -> DraftResult: ...  # F4
