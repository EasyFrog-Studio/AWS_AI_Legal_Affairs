# -*- coding: utf-8 -*-
"""留出測試集評測:讀卷證 PDF 資料夾組 Case、跑 pipeline,對照人工標籤算 F1-F4 各項指標。

每個案例資料夾底下的四個卷證檔對應固定槽位:01=訴願書、02=原處分書、03=送達證書、
04=訴願答辯書。03/04 可缺(該槽不建),01/02 缺一即無法組案,該案以 status=error 記錄、
不中斷整批評測。

用法:

    cd backend
    AI_PROVIDER=mock python tools/eval_holdout.py --cases <資料夾> --labels <labels.json> [--out report.json]

provider 依環境變數 AI_PROVIDER(mock|aws|local)建構;aws/local 需要對應憑證/服務,
在此之前先以 mock 驗證整條評測機制本身。

seconds 欄位是本機跑 run_case 的牆鐘時間,不是承辦人撰擬決定書所需的人工時間,兩者不可互相推論。
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.models import Case, CaseDocument, DocumentCheck, build_input_text, parse_clause  # noqa: E402
from app.pdf_extract import extract_text_quality  # noqa: E402
from app.pipeline import inadmissible_law_keys, run_case  # noqa: E402
from app.review import needs_review  # noqa: E402
from app.store import MemoryStore  # noqa: E402

# ---------- extract_law_keys ----------

_LAW_SUFFIXES_2CHAR = ("條例", "規則", "辦法", "準則", "細則", "標準", "要點", "通則")
# 「法」結尾字尾本身只占 1 字,前段至少留 1 字才滿足「長度 2-25」下限(如「同法」恰好 2 字);
# 其餘字尾占 2 字,前段可為 0 字(如「條例」本身)
_NAME_RE = (
    r"(?:[一-鿿]{1,39}法|[一-鿿]{0,38}(?:" + "|".join(_LAW_SUFFIXES_2CHAR) + "))"
)  # 上限 40 字:「公私場所固定污染源違反空氣污染防制法應處罰鍰額度裁罰準則」27 字,太短會從中段起配
_NUM_RE = r"(?:[0-9]+|[一二三四五六七八九十]+)"
_PAREN_RE = r"(?:[（(][^（）()]{0,20}[)）])?"  # 「空氣污染防制法（下稱空污法）第24條」的括號夾在名稱與「第」之間
_ARTICLE_RE = re.compile(rf"({_NAME_RE})[」』]?{_PAREN_RE}第({_NUM_RE})條(?:之({_NUM_RE}))?")  # 「全名」第5條:閉引號可夾在中間
_ALIAS_RE = re.compile(rf"({_NAME_RE})[（(](?:下稱|以下簡稱|簡稱)([一-鿿]{{2,12}})[)）]")

_BACKREF_NAMES = {"同法", "同條例", "同辦法", "該法", "本法", "本條例", "本辦法", "本準則", "本規則"}  # 不是法規名,須回溯前一個明確法規名

# 可疊加剝除的稱代/連接前綴,由長到短排序以優先吃掉多字詞(如「已違反」整組,不留半個字)
_NAME_PREFIXES = sorted(
    ["依", "按", "據", "查", "次按", "又", "惟", "依據", "參照", "違反", "已違反", "係違反",
     "即", "與", "及", "暨", "並", "或", "及其", "同", "條", "項", "款", "目",
     "前揭", "上揭", "前開", "上開", "此觀", "非屬", "符合", "其為", "為", "對於", "核與", "顯與", "即與"],
    key=len, reverse=True,
)
_CJK_RE = re.compile(r"[一-鿿]")
_LAW_TOKEN_RE = re.compile(r"法|條例|規則|辦法|準則|細則|標準|要點|通則")
_SUFFIX_TOKEN_RE = re.compile(r"(法|條例|規則|辦法|準則|細則|標準|要點|通則)$")
_MIN_FULL_REMAINDER = 6  # 語料贅字最長 5 字(自難認其為),複合全名餘部最短 9 字(建築物公共安全檢查)


def _name_like_remainder(remainder: str) -> bool:
    return bool(_LAW_TOKEN_RE.search(remainder))


def _is_subsequence(short: str, full: str) -> bool:
    it = iter(full)
    return all(ch in it for ch in short)
_QUOTED_TITLE_RE = re.compile(rf"[「『]({_NAME_RE})[」』]")  # 引號內的完整標題,是本文最可信的法規名來源

# 動詞類前綴可出現在名稱中段(前一條文與本條文之間無標點時),從最後一個切;連接詞不在此列,「土壤及地下水…」是真實法規名
_VERB_PREFIX_RE = re.compile(r".*(?:已違反|係違反|次按|依據|參照|違反|依|按|據|即|惟|又|之)")  # 不含「查」:「檢查」是名稱的一部分

_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _to_int(raw: str) -> int:
    """阿拉伯或中文數字(一~九十九)-> int。"""
    if raw.isdigit():
        return int(raw)
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = _CN_DIGIT.get(left, 1) if left else 1
        ones = _CN_DIGIT.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN_DIGIT.get(raw, 0)


def _strip_one_prefix(name: str) -> Optional[str]:
    for prefix in _NAME_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix):]
    return None


def _resolve_name(raw_name: str) -> tuple[str, bool]:
    """逐步剝前綴,每一步都先檢查是否落成「同法」類回溯標記;查不到可剝前綴就停在原地。
    回傳 (name, is_backref)。"""
    cut = _VERB_PREFIX_RE.match(raw_name)
    name = raw_name[cut.end():] if cut and cut.end() < len(raw_name) else raw_name
    while True:
        if name in _BACKREF_NAMES:
            return name, True
        stripped = _strip_one_prefix(name)
        if stripped is None:
            return name, False
        name = stripped


def extract_law_keys(text: str) -> list[str]:
    """決定書/草稿文字 -> 去重、保序的「法規名稱#條號」清單。規則見下。"""
    normalized = re.sub(r"\s+", "", text)  # 語料排版會把「第 1 9 條」拆開
    matches = list(_ARTICLE_RE.finditer(normalized))
    titles = set(_QUOTED_TITLE_RE.findall(normalized))
    # 「依空氣污染防制法(下稱空污法)」:全名側先剝前綴
    aliases = {short: _resolve_name(full)[0] for full, short in _ALIAS_RE.findall(normalized)}
    titles |= set(aliases.values())

    def resolve(raw: str) -> tuple[str, bool]:
        # 「且未涉及本法」:以回溯詞結尾的整段都是子句,不是法規名
        for backref in _BACKREF_NAMES:
            if raw.endswith(backref):
                return backref, True
        # 引號標題是名稱的後綴就整個採用,不做任何切割——真實標題可能含「違反」這類動詞
        hits = [t for t in titles if raw.endswith(t)]
        if hits:
            return max(hits, key=len), False
        name, is_backref = _resolve_name(raw)
        if name in aliases:
            return aliases[name], False
        if not is_backref:
            # 簡稱恰為唯一一個標題的後綴就展開;兩個以上不知指哪一個,保留簡稱
            expansions = [t for t in titles if t != name and t.endswith(name)]
            if len(expansions) == 1:
                return expansions[0], False
        return name, is_backref

    # 第一遍:左邊界乾淨(句首或非中文字之後)的名稱當本文的法規名詞典;曾被前綴詞(依/按/違反…)引出的
    # 是強證據。成員 A 以另一成員 B 結尾時,A 要嘛是句首贅字+B(「此觀訴願法」),要嘛是複合全名
    # (「建築物公共安全檢查簽證及申報辦法」);餘部含法規詞或 A 為強證據才視為全名,否則剔除
    candidates: set[str] = set(titles)
    strong: set[str] = set(titles)
    for match in matches:
        raw = match.group(1)
        left_clean = match.start() == 0 or not _CJK_RE.match(normalized[match.start() - 1])
        name, is_backref = resolve(raw)
        if left_clean and not is_backref and len(name) >= 2:
            candidates.add(name)
            if raw != name:
                strong.add(name)

    def is_full_name(long: str, short: str) -> bool:
        if long == short or not long.endswith(short):
            return False
        remainder = long[: -len(short)]
        # 強證據也要餘部夠長:「並無洗錢防制法」剝掉「並」剩「無」,仍是贅字;真實複合名餘部 9 字起
        return _name_like_remainder(remainder) or (long in strong and len(remainder) >= _MIN_FULL_REMAINDER)

    clean_names = {n for n in candidates if not any(o != n and n.endswith(o) and not is_full_name(n, o) for o in candidates)}

    def canonical(name: str) -> str:
        # 簡稱恰為唯一一個全名的後綴就展開;黏了贅字的名稱對到詞典裡最長的後綴;
        # 「空污法」這種無引入的縮寫,字序是唯一一個同尾綴全名的子序列才展開;都不成立就原樣保留,不猜
        ups = [c for c in clean_names if is_full_name(c, name)]
        if len(ups) == 1:
            return ups[0]
        if name not in clean_names:
            suffixes = [c for c in clean_names if name.endswith(c)]
            if suffixes:
                return max(suffixes, key=len)
        tail = _SUFFIX_TOKEN_RE.search(name)
        same_tail = [c for c in clean_names if c != name and tail and c.endswith(tail.group(1)) and _is_subsequence(name, c)]
        return same_tail[0] if len(same_tail) == 1 else name

    keys: list[str] = []
    last_law_name: Optional[str] = None
    for match in matches:
        raw_name, num_raw, sub_raw = match.group(1), match.group(2), match.group(3)
        name, is_backref = resolve(raw_name)
        if is_backref:
            if last_law_name is None:  # 前面沒有任何法規名可回溯,整筆丟棄
                continue
            resolved_name = last_law_name
        else:
            if len(name) < 2:  # 剝完前綴後太短,不是有效法規名
                continue
            resolved_name = canonical(name)
            last_law_name = resolved_name
        key = f"{resolved_name}#{_to_int(num_raw)}"
        if sub_raw:
            key += f"-{_to_int(sub_raw)}"
        keys.append(key)
    return list(dict.fromkeys(keys))  # 去重保序


# ---------- evaluate_case ----------

_CASE_TYPE_STRIP = ("違反", "事件", "令")


def _normalize_case_type(value: str) -> str:
    value = "".join(value.split())
    for token in _CASE_TYPE_STRIP:
        value = value.replace(token, "")
    return value


def _case_type_compatible(a: str, b: str) -> bool:
    na, nb = _normalize_case_type(a), _normalize_case_type(b)
    if not na or not nb:
        return False
    return na in nb or nb in na


def _case_hit(case: Case, label: dict) -> Optional[bool]:
    if case.f3 is None:
        return None
    decision_true = label.get("decision")
    label_case_type = label.get("case_type", "")
    for similar in case.f3[:3]:
        if similar.result == decision_true and _case_type_compatible(similar.case_type, label_case_type):
            return True
    return False


def _predicted_law_keys(case: Case) -> list[str]:
    if case.track == "admissible":
        return [f"{law.law_name}#{law.article_no}" for law in (case.f2 or [])[:10]]
    if case.track == "inadmissible":
        matched_clause = case.screening.matched_clause if case.screening else None
        return inadmissible_law_keys(matched_clause)
    return []


def _allowed_law_keys(case: Case) -> list[str]:
    if case.track == "admissible":
        return [f"{law.law_name}#{law.article_no}" for law in (case.f2 or [])]
    matched_clause = case.screening.matched_clause if case.screening else None
    return inadmissible_law_keys(matched_clause)


def evaluate_case(case: Case, label: dict) -> dict:
    """單案指標;case 為 run_case 跑完後 store.get 的結果。

    seconds 這裡固定填 None——evaluate_case 不計時,計時的責任在呼叫端 run(),因為
    要量的是 run_case 本身的牆鐘時間,不是 evaluate_case 這個純計算函式的時間。
    """
    done = case.status == "done"

    decision_true = label.get("decision")
    decision_pred = case.f4.draft_type if case.f4 is not None else None
    decision_hit = (decision_pred == decision_true) if (done and decision_pred is not None) else None

    clause_true = label.get("clause")
    clause_pred = None
    if case.screening is not None and not case.screening.passed:
        parsed = parse_clause(case.screening.matched_clause)
        clause_pred = f"77({parsed[1]})" if parsed else None
    clause_hit = (clause_pred == clause_true) if (done and clause_true is not None) else None

    truth_laws = set(label.get("laws") or [])
    law_truth_n = len(truth_laws)
    law_recall = None
    if done and truth_laws:
        pred_laws = set(_predicted_law_keys(case))
        law_recall = len(pred_laws & truth_laws) / len(truth_laws)

    case_hit = _case_hit(case, label) if done else None

    if case.f4 is not None:
        cited = extract_law_keys(case.f4.reason + case.f4.main_text)
        allowed = set(_allowed_law_keys(case))
        hallucinated = [key for key in cited if key not in allowed]
    else:
        cited = []
        hallucinated = []

    return {
        "case_dir": case.case_id,
        "status": case.status,
        "track": case.track,
        "decision_pred": decision_pred,
        "decision_true": decision_true,
        "decision_hit": decision_hit,
        "clause_pred": clause_pred,
        "clause_true": clause_true,
        "clause_hit": clause_hit,
        "law_recall": law_recall,
        "law_truth_n": law_truth_n,
        "case_hit": case_hit,
        "cited_in_text": len(cited),
        "hallucinated": hallucinated,
        "needs_review": needs_review(case),
        "seconds": None,
    }


# ---------- aggregate ----------


def _ratio(rows: list[dict], key: str) -> Optional[float]:
    values = [row[key] for row in rows if row[key] is not None]
    return sum(values) / len(values) if values else None


def aggregate(rows: list[dict]) -> dict:
    """整體指標;None 的單案不計入分母。"""
    n = len(rows)
    total_cited = sum(row["cited_in_text"] for row in rows)
    total_hallucinated = sum(len(row["hallucinated"]) for row in rows)

    return {
        "n": n,
        "decision_accuracy": _ratio(rows, "decision_hit"),
        "clause_accuracy": _ratio(rows, "clause_hit"),
        "law_recall_at_10": _ratio(rows, "law_recall"),
        "case_top3_hit_rate": _ratio(rows, "case_hit"),
        "citation_hallucination_rate": (total_hallucinated / total_cited) if total_cited else None,
        "needs_review_rate": (sum(1 for row in rows if row["needs_review"]) / n) if n else None,
        "error_n": sum(1 for row in rows if row["status"] != "done"),
    }


# ---------- run ----------

_SLOT_FILES = {
    "appeal": "01_訴願書.pdf",
    "disposition": "02_原處分書.pdf",
    "service": "03_送達證書.pdf",
    "answer": "04_訴願答辯書.pdf",
}
_REQUIRED_SLOTS = ("appeal", "disposition")
_CREATED_AT = "1970-01-01T00:00:00+00:00"  # 評測案不落正式 store,建案時間點無意義,取固定值


def _load_documents(case_path: Path) -> dict:
    documents = {}
    for slot, filename in _SLOT_FILES.items():
        path = case_path / filename
        if not path.is_file():  # 03/04 可缺,該槽不建、不報錯
            continue
        text = extract_text_quality(path.read_bytes()).text
        documents[slot] = CaseDocument(slot=slot, source="pdf", text=text, check=DocumentCheck(matched=True, method="rule"))
    return documents


def _render_table(rows: list[dict], agg: dict) -> str:
    header = "| case | status | track | decision(pred/true) | clause(pred/true) | law_recall | case_hit | cited | halluc | review | sec |"
    sep = "|" + "---|" * 11
    lines = [header, sep]
    for row in rows:
        lines.append(
            "| {case_dir} | {status} | {track} | {dp}/{dt} | {cp}/{ct} | {lr} | {ch} | {cited} | {halluc} | {nr} | {sec} |".format(
                case_dir=row["case_dir"], status=row["status"], track=row["track"],
                dp=row["decision_pred"], dt=row["decision_true"],
                cp=row["clause_pred"], ct=row["clause_true"],
                lr=row["law_recall"], ch=row["case_hit"], cited=row["cited_in_text"],
                halluc=len(row["hallucinated"]), nr=row["needs_review"], sec=row["seconds"],
            )
        )
    lines.append("")
    lines.append(
        "n={n} decision_accuracy={decision_accuracy} clause_accuracy={clause_accuracy} "
        "law_recall_at_10={law_recall_at_10} case_top3_hit_rate={case_top3_hit_rate} "
        "citation_hallucination_rate={citation_hallucination_rate} needs_review_rate={needs_review_rate} "
        "error_n={error_n}".format(**agg)
    )
    return "\n".join(lines)


def run(cases_dir: Path, labels: dict, provider, out_path: Optional[Path] = None) -> dict:
    """對 cases_dir 下每個案例資料夾跑 pipeline 並對照 labels 算指標;labels 缺資料夾名一律 raise。"""
    rows = []
    for case_path in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
        case_dir = case_path.name
        documents = _load_documents(case_path)
        if not documents:  # 四個槽檔一個都沒有的不是案例(參考資料夾),跳過但要說
            print(f"[skip] {case_dir}:無任何卷證槽檔")
            continue
        label = labels[case_dir]  # 缺資料夾名 -> KeyError,不靜默跳過

        if not all(slot in documents for slot in _REQUIRED_SLOTS):
            case = Case(
                case_id=case_dir, created_at=_CREATED_AT, title=case_dir, status="error",
                source="pdf", input_text="", error="缺訴願書或原處分書,無法組案",
            )
            rows.append(evaluate_case(case, label))
            continue

        case = Case(
            case_id=case_dir, created_at=_CREATED_AT, title=case_dir, source="pdf",
            input_text=build_input_text(documents), documents=documents,
        )
        store = MemoryStore()
        store.create(case)

        start = time.monotonic()
        run_case(case_dir, store, provider)
        elapsed = time.monotonic() - start

        row = evaluate_case(store.get(case_dir), label)
        row["seconds"] = elapsed
        rows.append(row)

    report = {"rows": rows, "aggregate": aggregate(rows)}
    print(_render_table(rows, report["aggregate"]))
    if out_path is not None:
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _build_provider():
    """provider 依 AI_PROVIDER 在此建構,不 import app.main(它 import 時就建立正式 store 與 FastAPI app)。"""
    if settings.AI_PROVIDER == "aws":
        from app.providers.aws import AWSProvider

        return AWSProvider()
    if settings.AI_PROVIDER == "local":
        from app.providers.local import LocalProvider

        return LocalProvider()
    from app.providers.mock import MockProvider

    return MockProvider()


def main() -> None:
    parser = argparse.ArgumentParser(description="留出測試集評測:跑 pipeline 並對照人工標籤計算 F1-F4 各項指標。")
    parser.add_argument("--cases", required=True, help="卷證資料夾根目錄,底下每個子資料夾是一案")
    parser.add_argument("--labels", required=True, help="labels.json 路徑")
    parser.add_argument("--out", help="report.json 輸出路徑,不給則只印表格")
    args = parser.parse_args()

    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    run(Path(args.cases), labels, _build_provider(), Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()
