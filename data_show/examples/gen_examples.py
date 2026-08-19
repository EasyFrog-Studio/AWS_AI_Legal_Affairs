# -*- coding: utf-8 -*-
"""
產生 2 份可上傳測試用的訴願書 PDF 範例(一受理一不受理)。

素材來源(取 appeal_text 欄位):
- 不受理:data_show/sample_appeals/b_overdue_77_2.json (77(2) 訴願逾期,廢棄物清理法)
- 受理:  data_show/sample_appeals/d_dismiss_waste.json (實體審理駁回,廢棄物清理法)

用 PyMuPDF(fitz)產生 A4 直式 PDF,標題「訴願書」+ 內文,自動分頁續印。
內文字體改用系統內嵌 TrueType 字體(微軟正黑體 msjh.ttc),因為 PyMuPDF 內建
CJK 字型(china-t)雖可正常「顯示」中文,但未提供 ToUnicode CMap,會導致
get_text() 抽出的文字亂碼、無法被下游 PyMuPDF 文字抽取系統正確讀取。
改用內嵌 TrueType 字體後,顯示與文字抽取皆正確。
"""
import json
import os

import fitz

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BASE_DIR, "..", "sample_appeals")

FONT_FILE = r"C:\Windows\Fonts\msjh.ttc"  # 微軟正黑體(Traditional Chinese)
FONT_NAME = "msjh"

PAGE_WIDTH, PAGE_HEIGHT = fitz.paper_size("a4")  # 595 x 842 pt
MARGIN = 56
TITLE_FONTSIZE = 20
BODY_FONTSIZE = 12
LINEHEIGHT = 1.7


def load_appeal_text(json_filename: str) -> str:
    path = os.path.join(SAMPLE_DIR, json_filename)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    text = data["appeal_text"]
    # 內文開頭已含「訴願書」標題行,因為我們會另外畫大標題,故移除重複的標題行
    prefix = "訴願書\n\n"
    if text.startswith(prefix):
        text = text[len(prefix):]
    return text


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


def build_pdf(appeal_text: str, out_path: str) -> None:
    doc = fitz.open()

    # ---- 第一頁:標題 + 內文開頭 ----
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_font(fontname=FONT_NAME, fontfile=FONT_FILE)

    title_rect = fitz.Rect(MARGIN, MARGIN, PAGE_WIDTH - MARGIN, MARGIN + 40)
    page.insert_textbox(
        title_rect,
        "訴願書",
        fontname=FONT_NAME,
        fontsize=TITLE_FONTSIZE,
        align=fitz.TEXT_ALIGN_CENTER,
    )

    body_top = MARGIN + 55
    body_rect = fitz.Rect(MARGIN, body_top, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - MARGIN)

    chunks = _split_to_fit(body_rect, appeal_text)

    for i, chunk in enumerate(chunks):
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

    # msjh.ttc 是完整字型集合檔,若整包內嵌會讓 PDF 高達數十 MB;
    # 先做字型子集化,只保留實際用到的字元,大幅縮小檔案。
    doc.subset_fonts()
    doc.save(out_path, garbage=4, deflate=True)
    doc.close()


def verify_pdf(path: str) -> str:
    """用 fitz 重新開啟 PDF 並抽取文字,回傳前 80 字供人工核對。"""
    doc = fitz.open(path)
    full_text = "".join(page.get_text() for page in doc)
    doc.close()
    assert any("\u4e00" <= ch <= "\u9fff" for ch in full_text), "抽取文字未偵測到中文字元"
    return full_text


def main():
    out_dir = BASE_DIR

    cases = [
        ("b_overdue_77_2.json", "範例1_不受理_訴願逾期.pdf"),
        ("d_dismiss_waste.json", "範例2_受理_廢棄物清理法.pdf"),
    ]

    # Windows 主控台 cp950 編碼在轉印含罕見標點(如 ○)的字串時會遺失資訊,
    # 故驗證結果一律寫入 UTF-8 log 檔,不依賴 print() 到終端機。
    log_lines = []
    for json_name, pdf_name in cases:
        appeal_text = load_appeal_text(json_name)
        out_path = os.path.join(out_dir, pdf_name)
        build_pdf(appeal_text, out_path)

        full_text = verify_pdf(out_path)
        log_lines.append(f"[OK] {pdf_name}")
        log_lines.append(f"     字數(fitz抽取): {len(full_text)}")
        log_lines.append(f"     前80字: {full_text[:80]!r}")
        log_lines.append("")

    log_path = os.path.join(out_dir, "verify_log.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    print("done, see verify_log.txt")


if __name__ == "__main__":
    main()
