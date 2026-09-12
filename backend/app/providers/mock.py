"""Mock AIProvider:樣本目錄每個 `{name}.json` = {appeal_text, expected:{f1,screening,f2,f3,f4}},`_fallback.json` 為未命中時的回應。"""
import json
import time
from pathlib import Path
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
)
from app.providers.aws import _REF_TOP_K, _TOP_K
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
                f"MockProvider: 找不到 {_FALLBACK_NAME}.json，樣本目錄={self._dir}"
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

    def assess_standing(self, info: CaseInfo, text: str) -> StandingAssessment:
        """樣本無 "standing" 鍵時回空 StandingAssessment(referenced_norm="",has_standing=None,
        即證據不足)——既有樣本沒有一件是§77(3)當事人適格案,補樣本時才需要加這個鍵,
        鍵的形狀是 {"referenced_norm": "...", "has_standing": true/false/null}。"""
        time.sleep(1)
        standing = self._match_by_text(text).get("standing") or {}
        return StandingAssessment(**standing)

    def recommend_laws(self, info: CaseInfo) -> list[LawRef]:
        time.sleep(1)
        # 樣本手寫五條,截到與真實 provider 同一個上限,否則兩種模式看到的筆數不一樣
        return [LawRef(**item) for item in self._match_by_info(info)["f2"]][:_TOP_K]

    def get_law_articles(self, keys: list[str]) -> list[LawRef]:
        """從所有樣本的 f2 彙整成條號索引;查無者比照真實 provider 回「未收錄」。"""
        time.sleep(1)
        index: dict[str, dict] = {}
        # 同一條號可能出現在多個樣本且內容不同,依檔名排序取最後一筆,結果才不隨字典順序漂移
        for name in sorted(self._samples):
            for item in self._samples[name]["expected"].get("f2", []):
                index[f"{item['law_name']}#{item['article_no']}"] = item
        refs = []
        for key in keys:
            item = index.get(key)
            if item:
                refs.append(LawRef(**item))
            else:
                law_name, _, article_no = key.partition("#")
                refs.append(
                    LawRef(
                        law_name=law_name,
                        article_no=article_no,
                        text="",
                        amend_date="未收錄",
                        source_key=None,
                        relevance="條號精查，樣本未收錄",
                    )
                )
        return refs


    def find_references(self, info: CaseInfo) -> list[ReferenceRef]:
        time.sleep(1)
        # 直接索引:樣本缺鍵要大聲壞掉,靜默回空清單會與「檢索後無結果」混為一談
        return [ReferenceRef(**item) for item in self._match_by_info(info)["f2_refs"]][:_REF_TOP_K]

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
