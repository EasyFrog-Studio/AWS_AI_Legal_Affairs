"""評測的比對與計分邏輯(純函式,不碰 IO、不呼叫模型)。

三格計分:correct / wrong / unsure。unsure 是系統誠實回報「判斷不出來」——它至少知道
自己不知道,與亂猜是兩回事,故獨立成格,不併入對錯。
"""
import re
import sys
import unicodedata
from pathlib import Path

# 日期正規化刻意共用 backend 的 app.dates.normalize_roc(而非像 clause_key 那樣自帶一份解析器):
# 答案鍵與系統輸出都得先換算成同一種民國日期寫法才能比對「同一天」,兩邊各自猜寫法反而更容易兜不攏。
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.dates import normalize_roc  # noqa: E402

CORRECT = "correct"
WRONG = "wrong"
UNSURE = "unsure"

# F1 prompt 要求「找不到明確依據就填未載明」;那是誠實回報,不是抽錯
_HONEST_MISS = {"未載明", "未收錄", ""}


def normalize(text: str) -> str:
    """去空白、統一全半形與各種連字號,再比對。公文書同一個文號會寫成「41-112」「41–112」。"""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[–—－~〜]", "-", s)
    # 決定書內文常把文號末尾的「號」省掉,卷證則寫全;同一份文書,不是抽錯
    return re.sub(r"號$", "", s)


def normalize_roc_date(text: str) -> str:
    """民國日期經 normalize_roc 轉成標準寫法「民國114年7月4日」再比對,寫法不同、同一天即相等;
    解析不出來就退回 normalize() 後的原字串比對,不猜。"""
    normalized = normalize_roc(str(text or ""))
    return normalized if normalized is not None else normalize(text)


def score_field(actual: str, expected: str, *, is_date: bool = False) -> str:
    """單一欄位三格計分。expected 為 None 代表這一欄不列入計分,呼叫端不該送進來。"""
    if normalize(actual) in {normalize(v) for v in _HONEST_MISS}:
        return UNSURE
    norm = normalize_roc_date if is_date else normalize
    return CORRECT if norm(actual) == norm(expected) else WRONG


def score_case_type(actual: str, expected: str) -> str:
    """案類:決定書寫「違反廢棄物清理法事件」,系統輸出「廢棄物清理法」,指的是同一個案類。
    只脫掉這層外殼再比等值,不做包含比對——包含比對會讓整段抄寫的失控輸出只因為裡面出現法規名就記成答對。"""
    a, e = _case_type_core(actual), _case_type_core(expected)
    if normalize(actual) in {normalize(v) for v in _HONEST_MISS | {"未分類"}}:
        return UNSURE
    return CORRECT if e and a == e else WRONG


def _case_type_core(text: str) -> str:
    """脫掉「違反…事件」的外殼,剩下的才是案類本身。"""
    return re.sub(r"事件$", "", re.sub(r"^違反", "", normalize(text)))


def score_decision(actual: str | None, expected: str) -> str:
    """決定類型三格計分。答案取自決定書主文,五值與 DraftResult.draft_type 同一組。

    只做等值比對,不做包含比對:「部分不受理部分駁回」含有「不受理」三字,包含比對會讓
    一個少判了駁回部分的答案記成答對。草稿不存在(F4 逾時或失控生成)記 unsure——
    系統那時並沒有主張任何決定類型。
    """
    if not (actual or "").strip():
        return UNSURE
    return CORRECT if normalize(actual) == normalize(expected) else WRONG


def score_screening(
    passed: bool, matched_clause: str | None, review_note: str, expected: dict
) -> str:
    """程序審查分流三格計分。

    review_note 非空代表系統要求人工確認——結論即使碰巧與答案相同也不算 correct,
    因為它並沒有主張那個結論;那正是 unsure 這一格存在的理由。
    """
    if (review_note or "").strip():
        return UNSURE
    if bool(passed) != bool(expected["passed"]):
        return WRONG
    if expected["passed"]:
        return CORRECT  # 受理案沒有款次可比
    return CORRECT if clause_key(matched_clause) == expected["clause"] else WRONG


def clause_key(matched_clause: str | None) -> str | None:
    """「77條第2款」/「77條第二款」-> "77(2)";解析不出回 None。
    刻意不 import backend 的 parse_clause:評測若與受測程式共用解析器,解析器錯了兩邊會一起錯。
    """
    s = normalize(matched_clause)
    m = re.search(r"(\d+)條第([0-9]+|[一二三四五六七八九十]+)款", s)
    if not m:
        return None
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    raw = m.group(2)
    clause = int(raw) if raw.isdigit() else digits.get(raw)
    return f"{m.group(1)}({clause})" if clause else None


def tally(results: list[str]) -> dict:
    """一組計分結果 -> 三格統計 + 誤判率。誤判那一格才是風險,單獨算出來。"""
    counts = {CORRECT: 0, WRONG: 0, UNSURE: 0}
    for r in results:
        counts[r] += 1
    total = len(results)
    return {
        **counts,
        "total": total,
        "wrong_rate": round(counts[WRONG] / total, 3) if total else 0.0,
    }
