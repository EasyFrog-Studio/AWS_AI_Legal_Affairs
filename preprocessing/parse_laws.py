"""解析 11 個法規 PDF -> data/output/law_chunks.jsonl + data/output/markdown/相關法規/*.md

背景/已知問題(見回報)：
common.extract_text() 用 page.get_text() 預設順序(依 PDF content stream 順序，非視覺位置)。
其中 4 個檔案(訴願法/行政程序法/洗錢防制法/審議規則，判斷依據是頁首含 " EN" 與
"所有條文" 字樣的新版排版)把每頁的「條號」文字方塊(位於左側頁邊，y 座標對齊該條內文
起始列)在 content stream 中排在該頁內文之前，導致 extract_text() 併出來的純文字會把
一整頁的條號清單("第 5 條\n第 6 條\n...")集中堆在該頁內文前面，條號與內文完全脫節，
無法用簡單正則切條。

驗證發現：用 PyMuPDF 的 page.get_text("text", sort=True)（依視覺位置由上到下、由左到右
排序）可讓條號正確對齊排回其內文正前方(章節標題亦同)，11 個檔案全部條數與預期值(共
2214 條)完全吻合、零重複、零缺漏。因此本檔改為直接呼叫 fitz 並加 sort=True 抽取全文，
不使用 common.extract_text()（common.py 依規定不可修改）；clean_filename / write_jsonl /
write_markdown / DATASET_DIR / OUTPUT_DIR 仍依規定直接沿用 common.py。
"""
import random
import re
import unicodedata
from pathlib import Path

import fitz  # PyMuPDF

from common import DATASET_DIR, OUTPUT_DIR, clean_filename, write_jsonl, write_markdown

LAW_DIR = DATASET_DIR / "相關法規"

EXPECTED_COUNTS = {
    "民法": 1439,
    "行政程序法": 176,
    "建築法": 123,
    "訴願法": 101,
    "空氣污染防制法": 100,
    "廢棄物清理法": 81,
    "行政罰法": 46,
    "行政執行法": 45,
    "噪音管制法": 37,
    "行政院及各級行政機關訴願審議委員會審議規則": 35,
    "洗錢防制法": 31,
}

PROCEDURE_LAWS = {"訴願法", "行政程序法", "行政院及各級行政機關訴願審議委員會審議規則", "行政執行法", "行政罰法"}
SUBSTANTIVE_LAWS = {"廢棄物清理法", "空氣污染防制法", "噪音管制法", "建築法", "洗錢防制法"}
GENERAL_LAWS = {"民法"}

NOISE_PREFIXES = ("所有條文", "法規名稱", "法規類別", "列印時間", "生效狀態")

CHAPTER_RE = re.compile(
    r"^[ \t]*第[ \t]*([一二三四五六七八九十百千零]+(?:之[一二三四五六七八九十百千零]+)?)"
    r"[ \t]*(編|章|節|款|目)[ \t]*([^\n]*)$",
    re.MULTILINE,
)
ARTICLE_RE = re.compile(r"^[ \t]*第[ \t]*(\d+(?:-\d+)?)[ \t]*條", re.MULTILINE)
NEWLINE_TRIGGER_RE = re.compile(r"^(\d+[\s　]|[一二三四五六七八九十百千]+、|（[一二三四五六七八九十百千]+）)")


def law_type_of(law_name: str) -> str:
    if law_name in PROCEDURE_LAWS:
        return "程序法"
    if law_name in SUBSTANTIVE_LAWS:
        return "實體法"
    if law_name in GENERAL_LAWS:
        return "普通法"
    raise ValueError(f"未裁決的 law_type: {law_name}")


def extract_sorted_text(pdf_path: Path) -> str:
    """依視覺位置排序抽取全文(解決條號/章節與內文順序脫節問題，見檔頭說明)。"""
    with fitz.open(pdf_path) as doc:
        text = "\n".join(page.get_text("text", sort=True) for page in doc)
    return unicodedata.normalize("NFC", text)


def extract_amend_date(text: str) -> str:
    m = re.search(r"修正日期[：:]\s*([^\n]+)", text)
    return m.group(1).strip() if m else "未載明"


def strip_noise(text: str) -> str:
    kept = []
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith(NOISE_PREFIXES):
            continue
        kept.append(line)
    return "\n".join(kept)


def clean_body(raw: str) -> str:
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    if not lines:
        return ""
    out = [lines[0]]
    for l in lines[1:]:
        if NEWLINE_TRIGGER_RE.match(l):
            out.append(l)
        else:
            out[-1] += l
    return "\n".join(out)


def split_articles(text: str) -> list[dict]:
    """回傳 [{"article_no":..., "chapter":..., "body":...}, ...]，依文中出現順序。"""
    splits = []
    for m in CHAPTER_RE.finditer(text):
        title = m.group(3).strip()
        label = f"第{m.group(1)}{m.group(2)}"
        if title:
            label += f" {title}"
        splits.append((m.start(), m.end(), "chapter", label))
    for m in ARTICLE_RE.finditer(text):
        splits.append((m.start(), m.end(), "article", m.group(1)))
    splits.sort(key=lambda x: x[0])

    articles = []
    current_chapter = ""
    for i, (start, end, kind, val) in enumerate(splits):
        if kind == "chapter":
            current_chapter = val
        else:
            content_end = splits[i + 1][0] if i + 1 < len(splits) else len(text)
            body = clean_body(text[end:content_end])
            articles.append({"article_no": val, "chapter": current_chapter, "body": body})
    return articles


def build_markdown(law_name: str, amend_date: str, articles: list[dict]) -> str:
    lines = [f"# {law_name}", "", f"修正日期：{amend_date}", ""]
    last_chapter = None
    for a in articles:
        if a["chapter"] and a["chapter"] != last_chapter:
            lines.append(f"### {a['chapter']}")
            lines.append("")
            last_chapter = a["chapter"]
        lines.append(f"## 第 {a['article_no']} 條")
        lines.append("")
        lines.append(a["body"])
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    pdf_files = sorted(LAW_DIR.glob("*.pdf"))
    all_chunks = []
    per_law_stats = []
    skipped = []

    for pdf_path in pdf_files:
        clean_name = clean_filename(pdf_path.name)
        law_name = clean_name[: -len(".pdf")]

        raw_text = extract_sorted_text(pdf_path)
        amend_date = extract_amend_date(raw_text)
        cleaned_text = strip_noise(raw_text)
        articles = split_articles(cleaned_text)

        if not articles:
            skipped.append(law_name)
            continue

        law_type = law_type_of(law_name)
        for a in articles:
            text = f"{law_name} 第 {a['article_no']} 條"
            if a["chapter"]:
                text += f"（{a['chapter']}）"
            text += "\n" + a["body"]
            all_chunks.append(
                {
                    "id": f"{law_name}#{a['article_no']}",
                    "text": text,
                    "metadata": {
                        "law_name": law_name,
                        "article_no": a["article_no"],
                        "chapter": a["chapter"],
                        "amend_date": amend_date,
                        "law_type": law_type,
                        "doc_kind": "法規",
                    },
                }
            )

        write_markdown("相關法規", law_name, build_markdown(law_name, amend_date, articles))

        expected = EXPECTED_COUNTS.get(law_name)
        per_law_stats.append((law_name, len(articles), expected, amend_date))

    out_path = OUTPUT_DIR / "law_chunks.jsonl"
    write_jsonl(out_path, all_chunks)

    # ---- 驗證統計 ----
    print("=== 各法規條數 vs 預期 ===")
    total_actual = 0
    total_expected = 0
    for law_name, actual, expected, amend_date in sorted(per_law_stats):
        diff = actual - expected if expected is not None else None
        total_actual += actual
        total_expected += expected or 0
        flag = "OK" if diff == 0 else f"DIFF={diff}"
        print(f"  {law_name}: 實際={actual} 預期={expected} [{flag}] 修正日期={amend_date}")

    print(f"\n總條數: 實際={total_actual} 預期={total_expected} 差={total_actual - total_expected}")

    ids = [c["id"] for c in all_chunks]
    print(f"總 chunk 數(=行數): {len(all_chunks)}, 唯一 id 數: {len(set(ids))}")
    if len(ids) != len(set(ids)):
        dup = [i for i in set(ids) if ids.count(i) > 1]
        print(f"  !! 重複 id: {dup[:10]}")

    if skipped:
        print(f"\n跳過(無法解析出任何條文): {skipped}")

    print(f"\n輸出檔案: {out_path}")

    print("\n=== 隨機抽 5 條 ===")
    random.seed(42)
    for c in random.sample(all_chunks, min(5, len(all_chunks))):
        print(f"  id={c['id']}")
        print(f"  text[:80]={c['text'][:80]!r}")
        print()


if __name__ == "__main__":
    main()
