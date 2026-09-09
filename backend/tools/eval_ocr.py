# -*- coding: utf-8 -*-
"""量中文 OCR 的辨識率(實作計畫 Ticket 4d)。

「中文 OCR 準不準」不必等到有 AWS 憑證才知道:data_show/examples 的示範 PDF 是合成的,
原文即 ground truth,所以字元錯誤率算得出來。做法是把示範 PDF 重新 render 成無文字層的
影像版 PDF(模擬掃描件)→ 走 app/ocr.py → 與原文逐字比對。

用法(local 模式,需先啟動 ollama 並拉好視覺模型):

    cd backend
    AI_PROVIDER=local ../.venv/Scripts/python.exe tools/eval_ocr.py

輸出兩個數字,兩者都要看:
1. CER(字元錯誤率):整體品質。
2. 日期欄位逐欄正確率:關鍵指標。整體 CER 90% 但把「5月3日」讀成「5月8日」一樣是災難——
   這套系統的正確性建立在日期上。

⚠️ 這個量測只涵蓋合成 PDF(字型工整、無歪斜、無手寫)。真實掃描件的辨識率仍未量測,
兩者不可互相推論。量到的數字寫進 Content/ai-providers.md;沒量到之前,OCR 這條路
只能寫「跑得動」,不得寫成「可用」。
"""
import re
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ocr import get_ocr_client, ocr_pdf  # noqa: E402
from app.pdf_extract import extract_text  # noqa: E402

_EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "data_show" / "examples"
_DATE_RE = re.compile(r"\d{2,3}年\d{1,2}月\d{1,2}日")
_RENDER_DPI = 200


def to_image_only_pdf(pdf_bytes: bytes) -> bytes:
    """把有文字層的 PDF 逐頁 render 成影像後重組,產出無文字層版本(模擬掃描件)。"""
    source = fitz.open(stream=pdf_bytes, filetype="pdf")
    target = fitz.open()
    for page in source:
        pixmap = page.get_pixmap(dpi=_RENDER_DPI)
        new_page = target.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=pixmap.tobytes("png"))
    out = target.tobytes()
    source.close()
    target.close()
    return out


def _normalize(text: str) -> str:
    """比對前只去空白:標點與全形字都算在錯誤率內,那些也是抽取層要看的字。"""
    return "".join(text.split())


def cer(reference: str, hypothesis: str) -> float:
    """字元錯誤率(Levenshtein / 參考長度)。逐列 DP,長文書也不會吃掉記憶體。"""
    ref, hyp = _normalize(reference), _normalize(hypothesis)
    if not ref:
        return 0.0
    previous = list(range(len(hyp) + 1))
    for i, ref_char in enumerate(ref, start=1):
        current = [i]
        for j, hyp_char in enumerate(hyp, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (ref_char != hyp_char),
                )
            )
        previous = current
    return previous[-1] / len(ref)


def date_accuracy(reference: str, hypothesis: str) -> tuple[int, int]:
    """(正確讀出的日期數, 原文日期總數)。日期是整套期間計算的輸入,單獨看。"""
    expected = _DATE_RE.findall(_normalize(reference))
    found = _DATE_RE.findall(_normalize(hypothesis))
    hit = sum(1 for value in expected if value in found)
    return hit, len(expected)


def main() -> None:
    client = get_ocr_client()
    total_dates = total_hits = 0
    for pdf_path in sorted(_EXAMPLES_DIR.rglob("*.pdf")):
        original = pdf_path.read_bytes()
        reference = extract_text(original)
        recognised = ocr_pdf(to_image_only_pdf(original), client)

        hits, dates = date_accuracy(reference, recognised)
        total_hits, total_dates = total_hits + hits, total_dates + dates
        rel = pdf_path.relative_to(_EXAMPLES_DIR)
        print(f"{rel}\n  CER={cer(reference, recognised):.3f}  日期 {hits}/{dates}")

    if total_dates:
        print(f"\n日期逐欄正確率:{total_hits}/{total_dates} = {total_hits / total_dates:.3f}")


if __name__ == "__main__":
    main()
