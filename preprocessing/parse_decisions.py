"""解析歷史訴願決定書 PDF → data/output/case_chunks.jsonl + data/output/markdown/歷史訴願決定書/*.md

檔名格式:序號.年度-案件類型-訴願法條-爭點-決定結果.pdf
檔頭固定六欄:案號 / 要旨 / 發文日期 / 發文字號 / 相關法條 / 全文
全文內以「主    文」「事    實」「理    由」(字元間夾雜半形空白)標記段落。
"""
import re
from collections import Counter
from pathlib import Path

from common import DATASET_DIR, OUTPUT_DIR, clean_filename, extract_text, write_jsonl, write_markdown

CASE_DIR = DATASET_DIR / "歷史訴願決定書"

FILENAME_RE = re.compile(r"^(?P<seq>\d+)\.(?P<year>\d+)年-(?P<rest>.+)$")

HEADER_LABEL_ORDER = ["案號", "要旨", "發文日期", "發文字號", "相關法條", "全文"]
HEADER_LABEL_PATTERNS = {
    "案號": re.compile(r"案\s*號[：:]"),
    "要旨": re.compile(r"要\s*旨[：:]"),
    "發文日期": re.compile(r"發文日期[：:]"),
    "發文字號": re.compile(r"發文字號[：:]"),
    "相關法條": re.compile(r"相關法條[：:]"),
    "全文": re.compile(r"全\s*文[：:]"),
}

SECTION_RE = re.compile(r"^[ \t]*(主[ \t]+文|事[ \t]+實|理[ \t]+由)[ \t]*$", re.MULTILINE)

SIGNATURE_RE = re.compile(r"訴願審議委員會主任委員")


def clean_case_type(raw: str) -> str:
    s = raw
    if s.startswith("違反"):
        s = s[len("違反"):]
    if s.endswith("事件"):
        s = s[: -len("事件")]
    return s


def parse_filename(pdf_path: Path):
    """回傳 (info dict, error str|None)。info 含 seq/year/case_type/appeal_article/issue/result。"""
    clean_name = clean_filename(pdf_path.name)
    stem = clean_name[:-4] if clean_name.lower().endswith(".pdf") else clean_name
    m = FILENAME_RE.match(stem)
    if not m:
        return None, f"檔名不符「序號.年度-...」基本格式: {pdf_path.name}"
    year = m.group("year")
    rest = m.group("rest")
    parts = rest.split("-")
    if len(parts) == 4:
        case_type_raw, appeal_article, issue, result = parts
    elif len(parts) == 3:
        case_type_raw, appeal_article, result = parts
        issue = ""
    else:
        return None, f"檔名切出 {len(parts)} 段(預期 3 或 4 段): {pdf_path.name}"
    info = {
        "seq": m.group("seq"),
        "year": year,
        "case_type": clean_case_type(case_type_raw),
        "appeal_article": appeal_article,
        "issue": issue,
        "result": result,
    }
    return info, None


def parse_header(text: str) -> dict:
    """依六個檔頭標籤依序切出欄位值(每個標籤只取「上一標籤之後」第一次出現的位置,
    避免內文重複出現的「案號：」等字樣被誤認為新的標籤)。"""
    positions = {}
    pos = 0
    for key in HEADER_LABEL_ORDER:
        m = HEADER_LABEL_PATTERNS[key].search(text, pos)
        positions[key] = m
        if m:
            pos = m.end()
    found_keys = [k for k in HEADER_LABEL_ORDER if positions[k] is not None]
    fields = {}
    for i, key in enumerate(found_keys):
        start = positions[key].end()
        end = positions[found_keys[i + 1]].start() if i + 1 < len(found_keys) else len(text)
        fields[key] = text[start:end].strip()
    return fields


def split_sections(full_text: str) -> dict:
    """切出 主文/事實/理由;切不出來則整篇回傳 {"全文": ...}。"""
    sig = SIGNATURE_RE.search(full_text)
    body = full_text[: sig.start()] if sig else full_text
    matches = list(SECTION_RE.finditer(body))
    if not matches:
        stripped = body.strip()
        return {"全文": stripped} if stripped else {}
    sections = {}
    for i, mm in enumerate(matches):
        name = re.sub(r"[ \t]+", "", mm.group(1))
        start = mm.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        if content:
            sections[name] = content
    return sections


def main():
    pdf_files = sorted(CASE_DIR.glob("*/*.pdf"))

    rows = []
    filename_failures = []  # (filename, reason)
    field_issues = []  # (filename, reason)
    section_downgrades = []  # filenames where whole doc fell back to 全文
    result_counter = Counter()
    section_counter = Counter()
    success = 0

    for pdf_path in pdf_files:
        info, err = parse_filename(pdf_path)
        if info is None:
            filename_failures.append((pdf_path.name, err))
            continue

        text = extract_text(pdf_path)
        header = parse_header(text)

        case_no = header.get("案號")
        if not case_no:
            case_no = header.get("發文字號")
            if case_no:
                field_issues.append((pdf_path.name, f"無案號欄,以發文字號代替: {case_no}"))
            else:
                filename_failures.append((pdf_path.name, "無案號欄也無發文字號欄,無法產生 case_no"))
                continue

        full_text = header.get("全文", "")
        if not full_text:
            filename_failures.append((pdf_path.name, "全文欄位為空,無法解析內文"))
            continue

        sections = split_sections(full_text)
        if list(sections.keys()) == ["全文"]:
            section_downgrades.append(pdf_path.name)

        reason_text = sections.get("理由") or sections.get("全文")
        if not reason_text:
            field_issues.append((pdf_path.name, "理由欄(或全文)為空,不符驗收條件"))

        five = [info["year"], info["case_type"], info["appeal_article"], info["issue"], info["result"]]
        if not all(five):
            field_issues.append((pdf_path.name, f"metadata 五要素缺漏: year/case_type/appeal_article/issue/result={five}"))

        clean_name = clean_filename(pdf_path.name)
        stem = clean_name[:-4] if clean_name.lower().endswith(".pdf") else clean_name

        for section_name, content in sections.items():
            chunk_id = f"{case_no}#{section_name}"
            header_line = f"【{info['year']}年-{info['case_type']}-{info['appeal_article']}-{info['issue']}-{info['result']}】{section_name}欄"
            chunk_text = f"{header_line}\n{content}"
            metadata = {
                "year": info["year"],
                "case_type": info["case_type"],
                "appeal_article": info["appeal_article"],
                "issue": info["issue"],
                "result": info["result"],
                "section": section_name,
                "case_no": case_no,
                "source_file": clean_name,
            }
            rows.append({"id": chunk_id, "text": chunk_text, "metadata": metadata})
            section_counter[section_name] += 1

        result_counter[info["result"]] += 1
        success += 1

        md_lines = [
            f"# {clean_name}",
            "",
            f"- 案號：{header.get('案號', '')}",
            f"- 要旨：{header.get('要旨', '')}",
            f"- 發文日期：{header.get('發文日期', '')}",
            f"- 發文字號：{header.get('發文字號', '')}",
            f"- 相關法條：{header.get('相關法條', '')}",
            "",
            "## 全文",
            "",
            full_text,
        ]
        write_markdown("歷史訴願決定書", stem, "\n".join(md_lines))

    write_jsonl(OUTPUT_DIR / "case_chunks.jsonl", rows)

    total = len(pdf_files)
    print(f"=== 解析結果:{success}/{total} 檔成功 ===")
    print()
    print(f"檔名解析失敗 / 無法產生內容 ({len(filename_failures)} 件):")
    for name, reason in filename_failures:
        print(f"  - {name}: {reason}")
    print()
    print(f"欄位缺漏 / 需注意 ({len(field_issues)} 件):")
    for name, reason in field_issues:
        print(f"  - {name}: {reason}")
    print()
    print(f"整篇降級為「全文」單一 section ({len(section_downgrades)} 件):")
    for name in section_downgrades:
        print(f"  - {name}")
    print()
    print("result 分布:")
    for k, v in sorted(result_counter.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print()
    print("section 分布:")
    for k, v in sorted(section_counter.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print()
    print(f"chunk 總數: {len(rows)}")


if __name__ == "__main__":
    main()
