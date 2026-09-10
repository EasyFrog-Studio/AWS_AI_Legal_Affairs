"""可讀字元比例判準:送達證書無法辨識判斷與 OCR 產出可用性判斷共用同一套門檻。"""
from app.text_quality import is_unreadable, readable_char_ratio


def test_normal_chinese_text_is_readable():
    text = "送達時間:中華民國114年5月28日上午10時30分,送達方式:寄存於派出所。"
    assert readable_char_ratio(text) > 0.9
    assert is_unreadable(text) is False


def test_garbled_text_is_unreadable():
    """模擬 OCR/編碼錯誤產出的亂碼:大量落在可讀字元集合以外的符號。"""
    text = "�@�i�D�@�ѡj\n\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd※★●◆■"
    assert is_unreadable(text) is True


def test_empty_text_is_unreadable():
    assert readable_char_ratio("") == 0.0
    assert is_unreadable("") is True
