"""案件處理 pipeline:F1 -> screening -> (F2 ->) F3 -> F4,逐階段落庫。"""
from app.providers.base import AIProvider
from app.store import CaseStore


def run_case(case_id: str, store: CaseStore, provider: AIProvider) -> None:
    case = store.get(case_id)
    if case is None:
        raise ValueError(f"case not found: {case_id}")

    try:
        info = provider.extract_case_info(case.input_text)
        store.update(case_id, {"f1": info, "current_stage": "screening"})

        screening = provider.screen_admissibility(info, case.input_text)
        store.update(case_id, {"screening": screening})

        if screening.passed:
            store.update(case_id, {"track": "admissible", "current_stage": "f2"})
            laws = provider.recommend_laws(info)
            store.update(case_id, {"f2": laws, "current_stage": "f3"})
        else:
            # 不通過:跳過 F2,直接找同款不受理案例
            store.update(case_id, {"track": "inadmissible", "current_stage": "f3"})
            laws = []

        similar_cases = provider.find_similar_cases(info, screening, case.input_text)
        store.update(case_id, {"f3": similar_cases, "current_stage": "f4"})

        draft = provider.generate_draft(info, screening, laws, similar_cases)
        store.update(case_id, {"f4": draft, "current_stage": "done", "status": "done"})

    except Exception as exc:  # noqa: BLE001 - pipeline 需捕捉任何例外落庫,不中斷背景任務
        store.update(case_id, {"status": "error", "error": str(exc)})
