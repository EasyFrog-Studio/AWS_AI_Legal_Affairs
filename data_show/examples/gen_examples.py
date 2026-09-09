# -*- coding: utf-8 -*-
"""產生開箱示範素材:每個示範案三份 PDF(訴願書／送達證書／原處分書)＋兩種送達證書變體。
文書原文在 demo_documents.py。字體用系統 msjh.ttc,fitz 內建 CJK 字型缺 ToUnicode,抽字會亂碼。
"""
import os

import fitz

from demo_documents import DEMO_CASES, SERVICE_VARIANTS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FONT_FILE = r"C:\Windows\Fonts\msjh.ttc"  # 微軟正黑體(Traditional Chinese)
FONT_NAME = "msjh"

PAGE_WIDTH, PAGE_HEIGHT = fitz.paper_size("a4")  # 595 x 842 pt
MARGIN = 56
TITLE_FONTSIZE = 18
BODY_FONTSIZE = 11
LINEHEIGHT = 1.6


def _fits(page_rect: fitz.Rect, text: str) -> bool:
    """用暫時文件量測某段文字是否能塞進一個 body 矩形內。"""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_font(fontname=FONT_NAME, fontfile=FONT_FILE)
    rc = page.insert_textbox(
        page_rect,
        text,
        fontname=FONT_NAME,
        fontsize=BODY_FONTSIZE,
        lineheight=LINEHEIGHT,
    )
    doc.close()
    return rc >= 0


def _split_to_fit(body_rect: fitz.Rect, text: str) -> list[str]:
    """把 text 切成多段,每段都能塞進 body_rect(供多頁續印使用)。"""
    chunks = []
    remaining = text
    while remaining:
        if _fits(body_rect, remaining):
            chunks.append(remaining)
            break
        lo, hi, best = 1, len(remaining), 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if _fits(body_rect, remaining[:mid]):
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        chunks.append(remaining[:best])
        remaining = remaining[best:]
    return chunks


def build_pdf(title: str, body_text: str, out_path: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_font(fontname=FONT_NAME, fontfile=FONT_FILE)

    page.insert_textbox(
        fitz.Rect(MARGIN, MARGIN, PAGE_WIDTH - MARGIN, MARGIN + 40),
        title,
        fontname=FONT_NAME,
        fontsize=TITLE_FONTSIZE,
        align=fitz.TEXT_ALIGN_CENTER,
    )

    body_rect = fitz.Rect(MARGIN, MARGIN + 55, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - MARGIN)
    for i, chunk in enumerate(_split_to_fit(body_rect, body_text)):
        if i > 0:
            page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
            page.insert_font(fontname=FONT_NAME, fontfile=FONT_FILE)
            body_rect = fitz.Rect(MARGIN, MARGIN, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - MARGIN)
        rc = page.insert_textbox(
            body_rect,
            chunk,
            fontname=FONT_NAME,
            fontsize=BODY_FONTSIZE,
            lineheight=LINEHEIGHT,
        )
        assert rc >= 0, f"文字仍未完全放入頁面,剩餘空間={rc}"

    # msjh.ttc 是完整字型集合檔,整包內嵌會讓 PDF 高達數十 MB;子集化只保留用到的字元
    doc.subset_fonts()
    doc.save(out_path, garbage=4, deflate=True)
    doc.close()


def verify_pdf(path: str) -> str:
    """用 fitz 重新開啟 PDF 並抽取文字;抽不出中文就是白做,直接讓它失敗。"""
    doc = fitz.open(path)
    full_text = "".join(page.get_text() for page in doc)
    doc.close()
    assert any("\u4e00" <= ch <= "\u9fff" for ch in full_text), "抽取文字未偵測到中文字元"
    return full_text


def main():
    log_lines = []

    for case in DEMO_CASES:
        case_dir = os.path.join(BASE_DIR, case["code"])
        os.makedirs(case_dir, exist_ok=True)
        for index, (doc_name, body) in enumerate(case["documents"].items(), start=1):
            pdf_name = f"{index:02d}_{doc_name}.pdf"
            out_path = os.path.join(case_dir, pdf_name)
            build_pdf(doc_name, body, out_path)
            full_text = verify_pdf(out_path)
            log_lines.append(f"[OK] {case['code']}/{pdf_name}  抽取字數={len(full_text)}")
        log_lines.append("")

    variant_dir = os.path.join(BASE_DIR, "送達證書變體")
    os.makedirs(variant_dir, exist_ok=True)
    for name, body in SERVICE_VARIANTS.items():
        out_path = os.path.join(variant_dir, f"{name}.pdf")
        build_pdf("送達證書", body, out_path)
        full_text = verify_pdf(out_path)
        log_lines.append(f"[OK] 送達證書變體/{name}.pdf  抽取字數={len(full_text)}")

    # Windows 主控台 cp950 轉印含罕見標點(如 ○)會遺失資訊,故驗證結果寫 UTF-8 log,不靠 print
    with open(os.path.join(BASE_DIR, "verify_log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    print("done, see verify_log.txt")


if __name__ == "__main__":
    main()
