"""前處理共用工具,parser 只 import 不修改。"""
import json
import re
import unicodedata
from pathlib import Path

import fitz  # PyMuPDF

DATASET_DIR = Path(__file__).resolve().parents[2] / "data" / "資料集"  # 四類來源 PDF 的共同父目錄
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "output"
MARKDOWN_DIR = OUTPUT_DIR / "markdown"


def clean_filename(name: str) -> str:
    """去除資料集檔名的 '.pdf 的副本.pdf' 後綴,回傳乾淨檔名(含 .pdf)。"""
    return re.sub(r"\.pdf 的副本\.pdf$", ".pdf", name)


def extract_text(pdf_path: Path) -> str:
    """抽取 PDF 全文文字(所有頁面串接,NFC 正規化)。"""
    with fitz.open(pdf_path) as doc:
        text = "\n".join(page.get_text() for page in doc)
    return unicodedata.normalize("NFC", text)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_markdown(category: str, stem: str, content: str) -> Path:
    """寫 markdown 到 data/output/markdown/{category}/{stem}.md,回傳路徑。"""
    path = MARKDOWN_DIR / category / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
