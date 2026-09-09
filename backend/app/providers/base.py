"""AIProvider 介面,三種 provider 共用簽名。"""
from abc import ABC, abstractmethod
from typing import Optional

from app.models import CaseInfo, DraftResult, LawRef, ScreeningResult, SimilarCase, StandingAssessment


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
    def recommend_laws(self, info: CaseInfo) -> list[LawRef]: ...  # F2

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
