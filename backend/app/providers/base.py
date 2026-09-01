"""AIProvider 介面,三種 provider 共用簽名。"""
from abc import ABC, abstractmethod

from app.models import CaseInfo, DraftResult, LawRef, ScreeningResult, SimilarCase


class AIProvider(ABC):
    @abstractmethod
    def extract_case_info(self, text: str) -> CaseInfo: ...  # F1

    @abstractmethod
    def screen_admissibility(self, info: CaseInfo, text: str) -> ScreeningResult: ...

    @abstractmethod
    def recommend_laws(self, info: CaseInfo) -> list[LawRef]: ...  # F2

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
