"""文字品質的共用判準:可讀字元比例。給送達證書無法辨識三態判斷與 OCR 產出
是否可用判斷共用同一套門檻,不要各寫一份——兩處判的是同一件事:這段文字是不是實質上能讀。
"""
import re

# 中文、英數字、常見標點與空白;OCR 產出的亂碼、掃描噪點常見大量落在此範圍外的符號
_READABLE_CHAR_RE = re.compile(r"[\u4e00-\u9fff0-9a-zA-Z、。,，．.:：;；()（）「」『』\-—/\s]")

# 低於此比例視為無法辨識(亂碼),不是單純字少——字少是另一個獨立門檻
UNREADABLE_RATIO_THRESHOLD = 0.5


def readable_char_ratio(text: str) -> float:
    """可讀字元佔全部字元的比例。空字串回 0.0(視為完全不可讀,但呼叫端通常會先擋空字串,
    因為「沒有這份文書」與「有文書但讀不出來」是不同的處理路徑,不該共用這支函式的回傳值判斷)。"""
    if not text:
        return 0.0
    readable = len(_READABLE_CHAR_RE.findall(text))
    return readable / len(text)


def is_unreadable(text: str) -> bool:
    """可讀字元比例低於門檻,視為無法辨識。"""
    return readable_char_ratio(text) < UNREADABLE_RATIO_THRESHOLD
