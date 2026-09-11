"""解析訴願答辯書 PDF → data/output/answer_chunks.jsonl

三類來源:data_show/test_cases 的合成測資、官方空白範本、彰化縣政府答辯要領手冊,
以 metadata.source_kind 區分(合成測資與真實語料混在一起檢索就是自問自答)。
段落標記「答 辯 聲 明」「事    實」「理    由」「證物：」(字元間夾雜半形空白),
切不出段落者(如要領手冊)退為全文,再依 chunk_text 分段。
"""
import re
from pathlib import Path

from common import OUTPUT_DIR, extract_text, write_jsonl
from parse_interpretations import chunk_text

REPO_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
TESTDATA_DIR = Path(__file__).resolve().parents[1] / "data_show" / "test_cases"
SAMPLE_DIR = REPO_DATA_DIR / "sample" / "04_訴願答辯書"
TEMPLATE_DIR = REPO_DATA_DIR / "範本來源"
# 原始下載檔是 Word 97 格式,已用 Word 轉存同名 PDF;此份與 sample/ 的行政院範例是同一份文件
# (文字相似度 0.996),兩份都收會讓同一個範本在檢索裡出現兩次
DUPLICATE_TEMPLATES = frozenset({"行政院訴願審議委員會-訴願答辯書-20260908"})

DOC_KIND = "訴願答辯書"

SECTION_RE = re.compile(
    r"^[ \t]*(答[ \t]*辯[ \t]*聲[ \t]*明|事[ \t]*實|理[ \t]*由|證[ \t]*物)[ \t]*[：:]?[ \t]*$",
    re.MULTILINE,
)
# 「此致」以下是收文機關與署名,不屬答辯內容
CLOSING_RE = re.compile(r"^[ \t]*此[ \t]*致", re.MULTILINE)

HEADER_LABELS = {
    "原處分機關": re.compile(r"原處分機關[ \t]*[：:][ \t]*(.+)"),
    "發文日期": re.compile(r"發文日期[ \t]*[：:][ \t]*(.+)"),
    "發文字號": re.compile(r"發文字號[ \t]*[：:][ \t]*(.+)"),
}


def parse_header(text: str) -> dict:
    """抓答辯書首部的機關與發文欄位;抓不到的欄位不放進 dict,不填空字串。"""
    fields = {}
    for key, pattern in HEADER_LABELS.items():
        m = pattern.search(text)
        if m and m.group(1).strip():
            fields[key] = m.group(1).strip()
    return fields


def split_sections(text: str) -> dict:
    """切出 答辯聲明/事實/理由/證物;切不出來則整篇回傳 {"全文": ...},全空回 {}。"""
    # 標題重覆與否要看整份原文再判斷:手冊收錄多份範例答辯書,每份後面都有「此致」,
    # 先截斷只會看到第一份而誤判成單一答辯書
    all_names = [re.sub(r"[ \t]+", "", m.group(1)) for m in SECTION_RE.finditer(text)]
    if not all_names or len(set(all_names)) != len(all_names):
        # 逐段切會讓同名段互相覆蓋,整份退為全文
        stripped = text.strip()
        return {"全文": stripped} if stripped else {}

    closing = CLOSING_RE.search(text)
    body = text[: closing.start()] if closing else text
    matches = list(SECTION_RE.finditer(body))
    names = [re.sub(r"[ \t]+", "", m.group(1)) for m in matches]
    sections = {}
    for i, (m, name) in enumerate(zip(matches, names)):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        if content:
            sections[name] = content
    return sections


def build_rows(source_kind: str, doc_id: str, title: str, text: str, source_file: str) -> list[dict]:
    """一份答辯書 → answer_chunks 契約的列;無文字層回空列表(由主流程計數)。"""
    sections = split_sections(text)
    if not sections:
        return []
    header = parse_header(text)
    rows = []
    for section_name, content in sections.items():
        pieces = chunk_text(content)
        for i, piece in enumerate(pieces, start=1):
            chunk_id = f"{doc_id}#{section_name}"
            if len(pieces) > 1:
                chunk_id = f"{chunk_id}p{i}"
            metadata = {
                "doc_kind": DOC_KIND,
                "source_kind": source_kind,
                "section": section_name,
                "title": title,
                "source_file": source_file,
            }
            if header.get("原處分機關"):
                metadata["agency"] = header["原處分機關"]
            for key, field in (("doc_date", "發文日期"), ("doc_no", "發文字號")):
                if header.get(field):
                    metadata[key] = header[field]
            rows.append(
                {
                    "id": chunk_id,
                    "text": f"【{DOC_KIND}-{source_kind}】{title} {section_name}欄\n{piece}",
                    "metadata": metadata,
                }
            )
    return rows


def collect_sources() -> list[tuple]:
    """(source_kind, doc_id, title, pdf_path) 清單。範本來源/ 的 .doc 無法解析,不列入。"""
    sources = []
    for pdf_path in sorted(TESTDATA_DIR.glob("*/04_訴願答辯書.pdf")):
        example = pdf_path.parent.name
        sources.append(("測資", f"答辯-{example}", f"TEST_DATA {example}", pdf_path))
    for pdf_path in sorted(SAMPLE_DIR.glob("*.pdf")):
        stem = pdf_path.stem
        # 彰化那份 83 頁是答辯要領手冊(含各款不受理答辯範例),不是可填的空白範本
        kind = "要領手冊" if "原則" in stem else "範本"
        sources.append((kind, f"答辯-{stem}", stem, pdf_path))
    for pdf_path in sorted(TEMPLATE_DIR.glob("*訴願答辯書*.pdf")):
        stem = pdf_path.stem
        if stem in DUPLICATE_TEMPLATES:
            continue
        sources.append(("範本", f"答辯-{stem}", stem, pdf_path))
    return sources


def main() -> None:
    rows = []
    empty = []
    for source_kind, doc_id, title, pdf_path in collect_sources():
        text = extract_text(pdf_path)
        built = build_rows(source_kind, doc_id, title, text, pdf_path.name)
        if not built:
            empty.append(pdf_path.name)
            continue
        rows.extend(built)
        sections = sorted({r["metadata"]["section"] for r in built})
        print(f"[{source_kind}] {title}: {len(built)} chunk,欄位={sections}")

    write_jsonl(OUTPUT_DIR / "answer_chunks.jsonl", rows)
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        print(f"!! 重複 id {len(dup)} 個: {dup[:5]}")
    if empty:
        print(f"無文字層而略過 ({len(empty)} 件): {empty}")
    print(f"[總計] chunk數={len(rows)},輸出於 {OUTPUT_DIR / 'answer_chunks.jsonl'}")


if __name__ == "__main__":
    main()
