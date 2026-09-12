"""評測執行器:拿官方訴願決定書當答案,量這套系統在真實卷證上的正確率。

手動執行,不進 pytest——它需要呼叫真實模型(aws 模式的 Bedrock 或 local 模式的 ollama),
而 `python -m pytest` 必須在沒有模型與憑證的機器上也跑得完。用法見同目錄 README.md。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

_EVAL_DIR = Path(__file__).resolve().parent
_CODE_ROOT = _EVAL_DIR.parent

sys.path.insert(0, str(_EVAL_DIR))
sys.path.insert(0, str(_CODE_ROOT / "backend"))
# 評測在主機上跑,而 provider 的預設位址 host.docker.internal 只有容器內解析得到
os.environ.setdefault("LOCAL_LLM_BASE_URL", "http://localhost:11434")
# .env 只有 compose 會自動注入,主機上跑要自己載;override=False,上面那行的位址優先
load_dotenv(_CODE_ROOT / ".env", override=False)
# .env 的 POSTGRES_URL 是給容器用的(host 為 compose 服務名);F2/F3 會真的查庫,
# 在主機上就得走對外 port,與 local_setup/ingest.py 同一個變數
if os.environ.get("POSTGRES_URL_HOST"):
    os.environ["POSTGRES_URL"] = os.environ["POSTGRES_URL_HOST"]

from app.config import settings  # noqa: E402
from app.models import CASE_INFO_DATE_FIELDS, Case, CaseDocument, CaseInfo, build_input_text  # noqa: E402
from app.pdf_extract import extract_text  # noqa: E402
from app.pipeline import run_case  # noqa: E402
from app.providers.aws import AWSProvider  # noqa: E402
from app.providers.base import AIProvider  # noqa: E402
from app.providers.local import LocalProvider  # noqa: E402
from app.store import MemoryStore  # noqa: E402

from scoring import (  # noqa: E402
    CORRECT, UNSURE, WRONG, score_case_type, score_decision, score_field, score_screening, tally,
)

# 語料已內建於 data_show/,--cases / --decisions / --out 只用來覆寫成除錯或替代語料的路徑
_DEFAULT_CASES_DIR = _CODE_ROOT / "data_show" / "test_cases"
_DEFAULT_DECISIONS_DIR = _CODE_ROOT / "data_show" / "decisions_114"
_DEFAULT_OUTPUT_DIR = _EVAL_DIR / "reports"
_ANSWER_KEY = _EVAL_DIR / "answer_key.json"


def _build_provider() -> AIProvider:
    if settings.AI_PROVIDER == "aws":
        return AWSProvider()
    if settings.AI_PROVIDER == "local":
        return LocalProvider()
    raise SystemExit("評測需要真實模型,AI_PROVIDER 須為 aws 或 local")


def _model_info() -> tuple[str, str]:
    if settings.AI_PROVIDER == "aws":
        return settings.BEDROCK_MODEL_ID, settings.AWS_REGION
    return settings.LOCAL_LLM_MODEL, settings.LOCAL_LLM_BASE_URL


_SLOT_FILES = {
    "appeal": "01_訴願書.pdf",
    "disposition": "02_原處分書.pdf",
    "service": "03_送達證書.pdf",
    "answer": "04_訴願答辯書.pdf",
}

# 線1 的三個計分層與線2,分開統計:混成一個總分會讓「擷取很準但分流會錯」看起來像中等偏好
LAYER_SCREENING = "分流"
LAYER_F1 = "F1 擷取"
LAYER_DEADLINE = "期間"
LAYER_DECISION = "決定書 F1"
# 不受理那一側的決定類型由 enforce_inadmissible_format 依分流結果決定,不是 F4 自己判的;
# 這一層量的是「最後送到承辦人手上的決定類型對不對」,不等於 F4 的獨立準確率
LAYER_DECISION_TYPE = "決定類型"


def _with_stage_name(stage: str, fn, *args):
    """pipeline 把所有例外壓成一句 status=error;沒有階段名就分不出是擷取掛了還是分流掛了。"""
    try:
        return fn(*args)
    except Exception as exc:
        raise RuntimeError(f"{stage} 失敗:{exc}") from exc


class StageNamedProvider(AIProvider):
    """把每一層的例外冠上階段名再往外丟,其餘一律轉給真的 provider。

    pipeline 把所有例外壓成一句 status=error,沒有階段名就分不出是擷取掛了還是生成掛了。
    六層全走真貨而不是讓 F2 之後回空:決定類型要量就得讓 F4 真的生成,而 F4 的輸入來自
    F2/F3 的檢索結果,抽掉檢索等於量一條產品上不存在的路徑。
    """

    def __init__(self, inner: AIProvider) -> None:
        self._inner = inner

    def extract_case_info(self, text):
        return _with_stage_name("F1", self._inner.extract_case_info, text)

    def screen_admissibility(self, info, text):
        return _with_stage_name("程序審查", self._inner.screen_admissibility, info, text)

    def assess_standing(self, info, text):
        return _with_stage_name("當事人適格", self._inner.assess_standing, info, text)

    def recommend_laws(self, info):
        return _with_stage_name("F2", self._inner.recommend_laws, info)

    def find_references(self, info):
        return _with_stage_name("F2+", self._inner.find_references, info)

    def get_law_articles(self, keys):
        return _with_stage_name("法條精查", self._inner.get_law_articles, keys)

    def find_similar_cases(self, info, screening, text):
        return _with_stage_name("F3", self._inner.find_similar_cases, info, screening, text)

    def generate_draft(self, info, screening, laws, cases):
        return _with_stage_name("F4", self._inner.generate_draft, info, screening, laws, cases)


def _roc(value: date | None) -> str:
    """DeadlineCheck 的欄位是 date;答案鍵寫民國。None 轉空字串,交由計分視為誠實的「抽不到」。"""
    return "" if value is None else f"{value.year - 1911}/{value.month}/{value.day}"


def _row(layer: str, field: str, actual, expected, result: str) -> dict:
    return {
        "layer": layer,
        "field": field,
        "actual": "" if actual is None else str(actual),
        "expected": "" if expected is None else str(expected),
        "result": result,
    }


def _load_case(name: str, example_dir: Path) -> Case:
    documents = {}
    for slot, filename in _SLOT_FILES.items():
        path = example_dir / filename
        if not path.exists():
            continue  # example3 卷內本來就沒有送達證書,缺槽是合法輸入
        documents[slot] = CaseDocument(slot=slot, source="pdf", text=extract_text(path.read_bytes()))
    if "appeal" not in documents:
        raise FileNotFoundError(f"{name} 缺訴願書,無法評測")
    return Case(
        case_id=name,
        created_at=datetime.now().isoformat(timespec="seconds"),
        title=name,
        status="processing",
        source="pdf",
        documents=documents,
        input_text=build_input_text(documents),
    )


def _extracted_value(info: CaseInfo | None, field: str) -> str:
    """答案鍵的欄位名必須真的是 CaseInfo 的欄位。抓不到就炸,不以空字串頂替——
    那會讓「答案鍵打錯字」在報告上偽裝成「系統誠實回報抽不到」。"""
    if field not in CaseInfo.model_fields:
        raise KeyError(f"答案鍵的欄位 {field} 不存在於 CaseInfo")
    return getattr(info, field) if info is not None else ""


def _score_fields(info: CaseInfo | None, expected: dict, layer: str) -> list[dict]:
    """F1 欄位計分。expected 中為 null 的欄位代表決定書沒寫或有歧義,整欄跳過;
    `_` 開頭的鍵是註記不是欄位。"""
    rows = []
    for field, want_value in expected.items():
        if want_value is None or field.startswith("_"):
            continue
        actual = _extracted_value(info, field)
        result = (
            score_case_type(actual, want_value)
            if field == "case_type"
            else score_field(actual, want_value, is_date=field in CASE_INFO_DATE_FIELDS)
        )
        rows.append(_row(layer, field, actual, want_value, result))
    return rows


def _score_case(case: Case, expected: dict) -> list[dict]:
    """一組合成卷證的三層計分。"""
    rows: list[dict] = []

    want = expected.get("screening")
    if want is not None:
        screening = case.screening
        actual = f"passed={screening.passed} clause={screening.matched_clause}" if screening else ""
        result = (
            score_screening(screening.passed, screening.matched_clause, screening.review_note, want)
            if screening
            else WRONG
        )
        rows.append(_row(LAYER_SCREENING, "受理與否/款次", actual, f"passed={want['passed']} clause={want['clause']}", result))

    want_decision = expected.get("decision")
    if want_decision is not None:
        actual = case.f4.draft_type if case.f4 else ""
        rows.append(_row(LAYER_DECISION_TYPE, "draft_type", actual, want_decision,
                         score_decision(actual, want_decision)))

    rows += _score_fields(case.f1, expected.get("f1") or {}, LAYER_F1)

    check = case.deadline
    for field, want_value in (expected.get("deadline") or {}).items():
        if want_value is None:
            continue
        actual = _roc(getattr(check, field) if check else None)
        rows.append(_row(LAYER_DEADLINE, field, actual, want_value, score_field(actual, want_value, is_date=True)))

    return rows


def _run_cases(provider: AIProvider, key: dict, only: str | None, cases_dir: Path) -> list[dict]:
    results = []
    for name, expected in key["cases"].items():
        if name.startswith("_") or (only and name != only):
            continue
        started = time.time()
        entry = {"name": name, "source_decision": expected.get("source_decision", ""), "note": expected.get("_note", "")}
        try:
            case = _load_case(name, cases_dir / name)
            store = MemoryStore()
            store.create(case)
            run_case(name, store, provider)
            done = store.get(name)
            if done.status == "error":
                raise RuntimeError(done.error or "pipeline 未說明的失敗")
            entry["rows"] = _score_case(done, expected)
            # 判錯時光看結論看不出是模型判錯還是保留條款擋下了覆寫,兩者要做的事完全不同
            entry["screening_review_note"] = done.screening.review_note if done.screening else ""
            entry["deadline_review_note"] = done.deadline.review_note if done.deadline else ""
        except Exception as exc:  # noqa: BLE001 - 跑不完是評測結果的一部分,要出現在報告裡而不是中斷整輪
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["rows"] = []
        entry["seconds"] = round(time.time() - started, 1)
        print(f"  {name}: {entry.get('error') or _line_summary(entry['rows'])}  ({entry['seconds']}s)")
        results.append(entry)
    return results


def _run_decisions(provider: AIProvider, key: dict, only: str | None, decisions_dir: Path) -> list[dict]:
    sources = {path.name[:2]: path for path in sorted(decisions_dir.glob("*.txt"))}
    results = []
    for doc_id, expected in key["decisions"].items():
        if doc_id.startswith("_") or (only and doc_id != only):
            continue
        path = sources.get(doc_id)
        started = time.time()
        entry = {"id": doc_id, "source": path.name if path else "", "note": expected.get("_note", ""), "rows": []}
        try:
            if path is None:
                raise FileNotFoundError(f"找不到編號 {doc_id} 的決定書全文")
            info = provider.extract_case_info(path.read_text(encoding="utf-8"))
            entry["rows"] = _score_fields(info, expected, LAYER_DECISION)
        except Exception as exc:  # noqa: BLE001 - 同上,單份失敗不該中斷整輪
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["rows"] = []  # 半套結果不計入題數,否則報告會一邊說「未產生任何答案」一邊拿它算分
        entry["seconds"] = round(time.time() - started, 1)
        print(f"  決定書 {doc_id}: {entry.get('error') or _line_summary(entry['rows'])}  ({entry['seconds']}s)")
        results.append(entry)
    return results


def _line_summary(rows: list[dict]) -> str:
    stats = tally([r["result"] for r in rows])
    return f"correct {stats['correct']} / wrong {stats['wrong']} / unsure {stats['unsure']}"


def _all_rows(entries: list[dict]) -> list[dict]:
    return [row for entry in entries for row in entry["rows"]]


def _summarize(rows: list[dict]) -> dict:
    layers = {}
    for layer in (LAYER_SCREENING, LAYER_DECISION_TYPE, LAYER_F1, LAYER_DEADLINE, LAYER_DECISION):
        picked = [r["result"] for r in rows if r["layer"] == layer]
        if picked:
            layers[layer] = tally(picked)
    layers["合計"] = tally([r["result"] for r in rows])
    return layers


_RESULT_MARK = {CORRECT: "✅", WRONG: "❌", UNSURE: "⚠️"}


def _markdown(report: dict) -> str:
    lines = [
        "# 評測報告",
        "",
        f"- 產生時間:{report['generated_at']}",
        f"- 受測模型:{report['model']}({report['provider']} 模式 @ {report['location']})",
        f"- 答案來源:官方訴願決定書逐字記載;決定書未載或記載有歧義的欄位不計分",
        f"- 計分三格:✅ correct 對 / ❌ wrong 抽錯或判錯 / ⚠️ unsure 系統自陳不確定(誠實回報,不算失分)",
        "",
        "## 總表",
        "",
        "| 層 | 對 | 錯 | 不確定 | 題數 | 誤判率 |",
        "|---|---|---|---|---|---|",
    ]
    for layer, stats in report["summary"].items():
        lines.append(
            f"| {layer} | {stats['correct']} | {stats['wrong']} | {stats['unsure']} | {stats['total']} | {stats['wrong_rate']} |"
        )

    errors = [e for e in report["cases"] + report["decisions"] if e.get("error")]
    if errors:
        lines += ["", "## 執行失敗(未產生任何答案,其欄位不計入題數)", ""]
        lines += [f"- {e.get('name') or e.get('id')}:{e['error']}" for e in errors]

    lines += ["", "## 線1:合成卷證(四份文件 → 分流 / F1 / 期間)", ""]
    for entry in report["cases"]:
        lines.append(f"### {entry['name']} — {entry['source_decision']}")
        if entry.get("note"):
            lines.append(f"> {entry['note']}")
        for label, field in (("程序審查保留", "screening_review_note"), ("期間保留", "deadline_review_note")):
            if entry.get(field):
                lines.append(f"> {label}:{entry[field]}")
        lines.append("")
        lines += _rows_table(entry["rows"])
        lines.append("")

    lines += [
        "## 線2:21 份真實 114 年決定書(F1 擷取與案類分類)",
        "",
        "> 這是刻意的 out-of-distribution 輸入:prompt 是為訴願書寫的,而決定書自己的抬頭就是"
        "「新北市政府」、自己的文號就是「新北府訴決字」,答案卻要原處分機關與原處分文號。"
        "這一線量的是擷取在陌生文體上的韌性,不是產品路徑上的正確率——產品路徑的輸入是線1 那四份卷證。",
        "",
        "| 編號 | 欄位 | 系統擷取 | 決定書記載 | 結果 |",
        "|---|---|---|---|---|",
    ]
    for entry in report["decisions"]:
        for row in entry["rows"]:
            lines.append(
                f"| {entry['id']} | {row['field']} | {row['actual']} | {row['expected']} | {_RESULT_MARK[row['result']]} |"
            )
    notes = [e for e in report["decisions"] if e.get("note")]
    if notes:
        lines += ["", "### 線2 逐份註記", ""]
        lines += [f"- {e['id']}:{e['note']}" for e in notes]
    lines.append("")
    return "\n".join(lines)


def _rows_table(rows: list[dict]) -> list[str]:
    if not rows:
        return ["(無計分項)"]
    out = ["| 層 | 欄位 | 系統輸出 | 決定書記載 | 結果 |", "|---|---|---|---|---|"]
    out += [
        f"| {r['layer']} | {r['field']} | {r['actual']} | {r['expected']} | {_RESULT_MARK[r['result']]} |" for r in rows
    ]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="訴願審理 AI 系統評測(依 AI_PROVIDER 選 aws 或 local)")
    parser.add_argument("--only", help="只跑指定組別(線1 用 example4,線2 用 07),除錯用")
    parser.add_argument("--skip-cases", action="store_true", help="略過線1 的合成卷證")
    parser.add_argument("--skip-decisions", action="store_true", help="略過線2 的 21 份決定書")
    parser.add_argument(
        "--cases", type=Path, default=_DEFAULT_CASES_DIR,
        help=f"卷證根目錄,底下為 example1..8(預設 {_DEFAULT_CASES_DIR})",
    )
    parser.add_argument(
        "--decisions", type=Path, default=_DEFAULT_DECISIONS_DIR,
        help=f"114 年決定書全文目錄(預設 {_DEFAULT_DECISIONS_DIR})",
    )
    parser.add_argument(
        "--out", type=Path, default=_DEFAULT_OUTPUT_DIR,
        help=f"報告輸出目錄(預設 {_DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    key = json.loads(_ANSWER_KEY.read_text(encoding="utf-8"))
    provider = StageNamedProvider(_build_provider())
    model, location = _model_info()

    print(f"模型 {model} @ {location}")
    cases = [] if args.skip_cases else _run_cases(provider, key, args.only, args.cases)
    decisions = [] if args.skip_decisions else _run_decisions(provider, key, args.only, args.decisions)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "provider": settings.AI_PROVIDER,
        "location": location,
        "authority": key["_authority"],
        "summary": _summarize(_all_rows(cases) + _all_rows(decisions)),
        "cases": cases,
        "decisions": decisions,
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "eval_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "eval_report.md").write_text(_markdown(report), encoding="utf-8")
    print(f"報告已寫入 {args.out / 'eval_report.md'}")
    for layer, stats in report["summary"].items():
        print(f"  {layer}: 對 {stats['correct']} / 錯 {stats['wrong']} / 不確定 {stats['unsure']}(共 {stats['total']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
