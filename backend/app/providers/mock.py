"""Mock AIProvider:樣本目錄每個 `{name}.json` = {appeal_text, expected:{f1,screening,f2,f3,f4}},`_fallback.json` 為未命中時的回應。"""
import json
import time
from pathlib import Path
from typing import Optional

from app.config import settings
from app.models import CaseInfo, DraftResult, LawRef, ScreeningResult, SimilarCase
from app.providers.base import AIProvider

_FALLBACK_NAME = "_fallback"


class MockProvider(AIProvider):
    def __init__(self, data_dir: Optional[str] = None) -> None:
        self._dir = Path(data_dir or settings.MOCK_DATA_DIR)
        self._samples: dict[str, dict] = self._load_samples()

    def _load_samples(self) -> dict[str, dict]:
        samples: dict[str, dict] = {}
        if self._dir.is_dir():
            for path in sorted(self._dir.glob("*.json")):
                with open(path, encoding="utf-8") as fh:
                    samples[path.stem] = json.load(fh)
        return samples

    def _fallback_expected(self) -> dict:
        fallback = self._samples.get(_FALLBACK_NAME)
        if fallback is None:
            raise RuntimeError(
                f"MockProvider: 找不到 {_FALLBACK_NAME}.json,樣本目錄={self._dir}"
            )
        return fallback["expected"]

    def _match_by_text(self, text: str) -> dict:
        """姓名/機關/案由各 1 分,取最高分且 >= 2 者;同案類樣本會並列 2 分,需以姓名決勝,不能首個命中即回傳。"""
        best: dict | None = None
        best_hits = 0
        for name, sample in self._samples.items():
            if name == _FALLBACK_NAME:
                continue
            f1 = sample.get("expected", {}).get("f1", {})
            keywords = [f1.get("appellant"), f1.get("agency"), f1.get("case_type")]
            hits = sum(1 for kw in keywords if kw and kw in text)
            if hits > best_hits:
                best, best_hits = sample["expected"], hits
        if best is not None and best_hits >= 2:
            return best
        return self._fallback_expected()

    def _match_by_info(self, info: CaseInfo) -> dict:
        """recommend_laws/generate_draft 未帶原文,以 F1 已擷取欄位反查對應樣本。"""
        for name, sample in self._samples.items():
            if name == _FALLBACK_NAME:
                continue
            f1 = sample.get("expected", {}).get("f1", {})
            if (
                f1.get("appellant") == info.appellant
                and f1.get("agency") == info.agency
                and f1.get("case_type") == info.case_type
            ):
                return sample["expected"]
        return self._fallback_expected()

    def extract_case_info(self, text: str) -> CaseInfo:
        time.sleep(1)
        return CaseInfo(**self._match_by_text(text)["f1"])

    def screen_admissibility(self, info: CaseInfo, text: str) -> ScreeningResult:
        time.sleep(1)
        return ScreeningResult(**self._match_by_text(text)["screening"])

    def recommend_laws(self, info: CaseInfo) -> list[LawRef]:
        time.sleep(1)
        return [LawRef(**item) for item in self._match_by_info(info)["f2"]]

    def find_similar_cases(
        self, info: CaseInfo, screening: ScreeningResult, text: str
    ) -> list[SimilarCase]:
        time.sleep(1)
        return [SimilarCase(**item) for item in self._match_by_text(text)["f3"]]

    def generate_draft(
        self,
        info: CaseInfo,
        screening: ScreeningResult,
        laws: list[LawRef],
        cases: list[SimilarCase],
    ) -> DraftResult:
        time.sleep(1)
        return DraftResult(**self._match_by_info(info)["f4"])
