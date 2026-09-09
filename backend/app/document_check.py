"""文件型態確認:上傳的內容是不是它自稱要放的那個槽位(訴願書/送達證書/原處分書)。
規則判斷不出來時退到 Gemini;兩層都判斷不出來就回 matched=None,交人工核對——
不可靜默當作已確認正確,那正是這道檢查存在的理由。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from app.config import settings
from app.models import DOCUMENT_SLOT_LABELS, DocumentCheck, DocumentSlot

# 「強特徵」命中即高度確定是這個槽位;「輔助特徵」單獨出現不足以認定,累加才計分。
_SLOT_KEYWORDS: dict[DocumentSlot, dict[str, list[str]]] = {
    "appeal": {
        "strong": ["訴願書"],
        # 後三項為訴願書範本獨有的欄名與結尾格式:訴願書必然引述它不服的處分書,
        # 「處分書/裁處書/罰鍰/事實/理由」因而全數命中而與原處分書同分,需要這類單向特徵破平手
        "supporting": [
            "訴願人",
            "原處分機關",
            "請求事項",
            "訴願理由",
            "收受或知悉行政處分日期",
            "訴願請求事項",
            "附送證件",
            "轉陳",
        ],
    },
    "service": {
        "strong": ["送達證書"],
        "supporting": ["送達時間", "送達方式", "寄存送達", "同居人", "受雇人", "留置", "郵局日戳"],
    },
    "disposition": {
        "strong": ["處分書", "裁處書"],
        # 非罰鍰型不利處分(駁回申請/撤銷資格等一般公文「函」)沒有「罰鍰」,加「說明」(公文慣用段名)
        # 補這類文件的輔助分數;不加「駁回」「撤銷」——這兩詞訴願人在訴願書裡同樣常寫(請求撤銷原處分)
        # 「受文者」是機關發文獨有的欄名(訴願書寫「此致」、送達證書寫「受送達人」),
        # 只以主旨/說明成文的函否則湊不到門檻
        "supporting": ["罰鍰", "主旨", "說明", "事實", "理由", "教示", "法令依據", "受文者"],
    },
}
_STRONG_WEIGHT = 3
_MATCH_THRESHOLD = 3  # 至少要有一個強特徵,或三個以上輔助特徵疊加,才算規則判斷得出來
# 公文標題排成「訴　　願　　書」、欄名跨行斷開,不去空白則強特徵永遠落空
_WHITESPACE = re.compile(r"\s+")


def _score(text: str, slot: DocumentSlot) -> int:
    kws = _SLOT_KEYWORDS[slot]
    score = _STRONG_WEIGHT if any(k in text for k in kws["strong"]) else 0
    score += sum(1 for k in kws["supporting"] if k in text)
    return score


def _rule_check(text: str, slot: DocumentSlot) -> DocumentCheck:
    """對三個槽位都計分,取分數最高者比對是否為期望的槽位;分數持平或全零則判斷不出來。"""
    compact = _WHITESPACE.sub("", text)
    scores = {s: _score(compact, s) for s in _SLOT_KEYWORDS}
    target_score = scores[slot]
    best_score = max(scores.values())
    top_slots = [s for s, sc in scores.items() if sc == best_score]

    if best_score < _MATCH_THRESHOLD:
        return DocumentCheck(matched=None, method="none", note="規則判斷特徵不足,無法確認文件類型")
    if len(top_slots) > 1:
        # 兩個以上槽位同分頂格(例如原處分書教示條款裡出現「訴願書」字樣,反過來也一樣),
        # 這代表文件本身就含混,不是「目標槽位恰好也是最高分之一」就能放心判定為真——
        # 交由 Gemini 判斷,不讓規則層在真正含混的情況下裝出一個確定的結論
        labels = "、".join(DOCUMENT_SLOT_LABELS[s] for s in top_slots)
        return DocumentCheck(
            matched=None, method="none", note=f"規則判斷特徵在{labels}之間並列,無法確認文件類型"
        )
    if top_slots[0] == slot:
        return DocumentCheck(matched=True, method="rule", note=f"符合{DOCUMENT_SLOT_LABELS[slot]}的文字特徵")
    return DocumentCheck(
        matched=False,
        method="rule",
        note=f"文字特徵更接近{DOCUMENT_SLOT_LABELS[top_slots[0]]},不是{DOCUMENT_SLOT_LABELS[slot]}",
    )


_GEMINI_PROMPT = (
    "以下是一份行政訴願案件卷宗中的文件原文,請判斷它屬於「訴願書」「送達證書」「原處分書」"
    "三者之一,或者三者都不是。只依據原文內容判斷,證據不足就回答「無法確認」,不要猜測。\n"
    "證據不足的情形包括但不限於:文件僅為附件清單、內容過於片段無法辨識主要意旨、"
    "或同時帶有兩種以上文件的特徵而無法分辨主體——遇到這些情形務必回答「無法確認」,不要挑一個看起來比較像的。\n\n"
    "【原文】\n{text}"
)
_GEMINI_LABEL_TO_SLOT = {v: k for k, v in DOCUMENT_SLOT_LABELS.items()}
# reasoning 排在 document_type 之前:JSON schema 引導模型逐屬性依序輸出,若結論欄位先出現,
# 模型容易先定案再回頭找理由佐證(硬凹),削弱「證據不足要回答無法確認」的指示效力;
# 先寫盤點證據的 reasoning 才是真正的思維鏈,讓後面的分類決定建立在前面寫下的證據上。
_GEMINI_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "document_type": {"type": "string", "enum": ["訴願書", "送達證書", "原處分書", "無法確認"]},
    },
    "required": ["reasoning", "document_type"],
}
_TEXT_HEAD_TAIL_CHARS = 2000  # 前後各取 2000 字,標題通常在頭、署名日期通常在尾,對辨識文件類型最有幫助


def _head_tail(text: str) -> str:
    """超長文字取頭尾各 2000 字接起來,不是單純砍前 4000 字——掃描件常見的前言/封面雜訊
    會佔掉前段篇幅,而文件標題在頭、印信署名日期在尾,兩端才是判斷文件類型最關鍵的部分。"""
    limit = _TEXT_HEAD_TAIL_CHARS
    if len(text) <= 2 * limit:
        return text
    return text[:limit] + "\n…(中略)…\n" + text[-limit:]


def _gemini_check(text: str, slot: DocumentSlot, http_client=None) -> DocumentCheck:
    """呼叫 Gemini 判斷文件類型。未設定金鑰或呼叫失敗一律回 matched=None,不讓外部服務的問題中斷建案。"""
    if not settings.GEMINI_API_KEY:
        return DocumentCheck(matched=None, method="none", note="規則判斷不出來,且未設定 Gemini API 金鑰,請人工核對")

    import httpx

    client = http_client or httpx.Client(timeout=30)
    try:
        resp = client.post(
            f"{settings.GEMINI_BASE_URL}/models/{settings.GEMINI_MODEL}:generateContent",
            params={"key": settings.GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": _GEMINI_PROMPT.format(text=_head_tail(text))}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": _GEMINI_SCHEMA,
                },
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        raw = payload["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - 外部服務的任何失敗都不得讓建案流程中斷
        return DocumentCheck(matched=None, method="none", note=f"Gemini 判斷失敗,請人工核對({exc})")

    doc_type = parsed.get("document_type", "")
    reasoning = parsed.get("reasoning", "")
    if doc_type == "無法確認" or doc_type not in _GEMINI_LABEL_TO_SLOT:
        return DocumentCheck(matched=None, method="gemini", note=reasoning or "Gemini 無法確認文件類型")
    matched_slot = _GEMINI_LABEL_TO_SLOT[doc_type]
    if matched_slot == slot:
        return DocumentCheck(matched=True, method="gemini", note=reasoning or f"Gemini 判斷為{doc_type}")
    return DocumentCheck(
        matched=False,
        method="gemini",
        note=reasoning or f"Gemini 判斷為{doc_type},不是{DOCUMENT_SLOT_LABELS[slot]}",
    )


def check_document(slot: DocumentSlot, text: str, *, http_client=None) -> DocumentCheck:
    """規則判斷優先(免費、不出網);判斷不出來(matched=None)才退到 Gemini。"""
    if not text.strip():
        return DocumentCheck(matched=None, method="none", note="文件內容為空,無法確認")
    result = _rule_check(text, slot)
    if result.matched is not None:
        return result
    return _gemini_check(text, slot, http_client=http_client)
