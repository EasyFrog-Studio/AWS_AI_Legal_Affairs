"""解析爬蟲訴願決定書 PDF 中缺 chunk 的 531 份補件 -> data/爬蟲集/chunk資料/訴願決定書-補件/決定書-{桶}.jsonl。
輸出契約與既有爬蟲語料(決定書-{桶}.jsonl)逐鍵相同,供 aws_setup/10_ingest_past_decisions.py 的
crawl_rows() 讀取;原始解析器原始碼已不在本機,本檔是依既有語料逆推契約後重寫的版本,不保證與原始
解析器逐案一致(尤其 paragraph_role 的細部歸類),但滿足同一份輸出契約。

版型統一:
- 標籤式(官方複本、部分再審網頁殼、部分桃園/臺中案):有「案號/要旨/發文日期/…/全文」或
  「案號/決定書日期/類型/原處分機關/內文」之類的欄位標籤,真正內文在「全文：」或「內文：」標籤之後
  (get_case_body 取最後一個符合的標籤,標籤前的網站導覽殘留與其他欄位值一併被跳過)。
- 直式(新北/桃園/臺中多數案件):內文直接開頭,無標籤,原樣使用。

主文/事實/理由三個標題允許字元間夾雜任意空白(含全形空白),且不要求獨佔一行——桃園/臺中版常見
主文/理由與內文同一行接排。搜尋一律從「決定如下」錨點之後開始,避免誤配內文引述提到的同名字樣;
主文內容超過 200 字視為誤配,回報失敗而非硬吞。事實段落找不到理由標題、但段落本身含「綜上論結」
或「決定如主文」等結論語時,判定為理由誤標事實,整段併入理由(見 fixtures/ty_facts_only.txt)。

paragraph_role 啟發式(抽樣既有語料約 200 筆歸納,依序判定,先中先得):
1. section=事實 → 一律「事實敘述」(既有語料 13,688 筆事實段落全部是此值,無例外)。
2. section=理由 且是該案最後一項、含「綜上論結」或「決定如主文」→「結語」。
3. 開頭為(可選「又/另/次/再」接)「按/依/查」,或含 ≥2 個「第N條」且不含案件專屬字樣
   (系爭/卷查/經查/查本件/查本案)→「法規引述」(單純轉錄法條,無本案事實)。
4. 含「主張…等語」或「…云云」(覆核並駁回訴願人某個具體主張的樣板句)→「其他項次」。
5. 開頭為「又/另/惟」但不接「按/依/查」(承接上一段而非另立新法條依據)→「接續段」。
6. 含案件專屬字樣(系爭/卷查/經查/查本件/查本案)→「本案論理」(法規涵攝到本案事實的實質論理)。
7. 有自己的項次記號(一/二/三…、(一)(二)…)→「其他項次」。
8. 其餘 → 「本案論理」。
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

from common import OUTPUT_DIR as OFFICIAL_OUTPUT_DIR

CASE_CHUNKS_PATH = OFFICIAL_OUTPUT_DIR / "case_chunks.jsonl"

SOURCE_PDF_DIR = Path(
    r"D:\Docker\爬蟲集-20260911T150629Z-1-001\爬蟲集\爬蟲原資料+官方資料\訴願決定書"
)
MISSING_LIST_PATH = Path(
    r"C:\Users\user\AppData\Local\Temp\claude\D--Docker-AWS-AI-Legal-Affairs-outerLayer"
    r"\80eb4944-11e5-4795-9109-d367d71dc194\scratchpad\missing_531.txt"
)
CRAWL_OUTPUT_DIR = Path(
    r"D:\Docker\爬蟲集-20260911T150629Z-1-001\爬蟲集\chunk資料\訴願決定書-補件"
)

BUCKETS = frozenset({
    "停車場法", "其他案型", "噪音管制法", "地價稅", "廢棄物清理法", "建築法",
    "水污染防治法", "洗錢防制法", "空氣污染防制法", "違章建築", "都市計畫法",
})

# 檔名:{年}-{月}-{日}_{案號}_{案由}[_官方].pdf;案號可含城市字母前綴(TC/TY),新北無前綴
FILENAME_RE = re.compile(
    r"^(?P<year>\d+)-(?P<month>\d+)-(?P<day>\d+)_(?P<case_no>[A-Za-z0-9]+)_(?P<case_type>.+?)(?P<official>_官方)?$"
)
CASE_NO_RE = re.compile(r"^(?P<letters>[A-Za-z]*)(?P<digits>\d+)$")

# 標籤式版型:「全文：」(新北)或「內文：」(桃園/臺中)之後才是真正的決定書內文
BODY_LABEL_RE = re.compile(r"[全內]\s*文[：:]")

def _loose(word: str) -> str:
    """PDF 版面換行常把詞語從中間切開(如「決\n定」「主任委\n員」),字元間一律容許任意空白。"""
    return r"\s*".join(re.escape(ch) for ch in word)


DECISION_ANCHOR_RE = re.compile(_loose("決定如下") + r"\s*[:：]?")
# 標題不能被誤配成一般詞語(如「正當理由」「事實上」),要求前一字不是中文字
_NOT_AFTER_HANZI = r"(?<![一-龥])"
MAIN_RE = re.compile(_NOT_AFTER_HANZI + _loose("主文"))
FACTS_RE = re.compile(_NOT_AFTER_HANZI + _loose("事實"))
REASON_RE = re.compile(_NOT_AFTER_HANZI + _loose("理由"))
CONCLUSION_RE = re.compile(_loose("綜上論結") + "|" + _loose("決定如主文"))
SIGNATURE_RE = re.compile(_loose("訴願審議委員會") + "(?:" + _loose("兼") + ")?" + _loose("主任委員"))
MAIN_TEXT_MAX_LEN = 200

# 頂層項次前一個字通常是句號或換行,但引號內容結束後接續下一項時是「」(見 tc_moneylaunder_inline.txt);
# 數字與頓號中間也可能被版面換行隔開(如「三\n、」),同樣容許任意空白
ITEM_SPLIT_RE = re.compile(r"(?:(?<=\A)|(?<=[。\n」]))\s*[一二三四五六七八九十百]+\s*、")

REBUTTAL_RE = re.compile(r"主張.*?(等語|云云)")
CITATION_OPEN_RE = re.compile(r"^(?:又|另|次|再)?(按|依|查)")
CASE_SPECIFIC_RE = re.compile(r"系爭|卷查|經查|查本件|查本案")
CONTINUATION_OPEN_RE = re.compile(r"^(?:又|另|惟)(?!.{0,2}(按|依|查))")
LEADING_ENUM_RE = re.compile(r"^(?:[一二三四五六七八九十百]+\s*、|[（(][一二三四五六七八九十百]+[）)])")
ARTICLE_RE = re.compile(r"第\s*\d+\s*條")

# 既有語料掃過全部 49,570 筆 chunk 的 related_laws 欄位只出現過這 26 個法規名(見診斷報告),
# 表示原始解析器本就是對著一份白名單抓,不是自由正則;自由正則會把「違反」「依」「查」「同」
# 之類的引述動詞一併吃進法規名(見 test_related_laws_* 系列踩過的坑),改用白名單才乾淨。
KNOWN_LAW_NAMES = sorted([
    "訴願法", "空氣污染防制法", "行政罰法", "廢棄物清理法", "行政程序法", "噪音管制法",
    "建築法", "洗錢防制法", "水污染防治法", "社會救助法", "土地稅法", "民法",
    "一般廢棄物回收清除處理辦法", "都市計畫法", "政府資訊公開法", "公寓大廈管理條例",
    "行政訴訟法", "工廠管理輔導法", "行政院及各級行政機關訴願審議委員會審議規則",
    "道路交通管理處罰條例", "行政執行法", "停車場法", "毒品危害防制條例", "都市更新條例",
    "地方制度法", "違反廢棄物清理法罰鍰額度裁罰準則",
], key=len, reverse=True)
_SAME_LAW_TOKENS = ("同法", "同條例", "同辦法", "同細則", "同準則")
_LAW_NAME_ALT = "|".join(re.escape(n) for n in KNOWN_LAW_NAMES + list(_SAME_LAW_TOKENS))
RELATED_LAW_RE = re.compile(r"(" + _LAW_NAME_ALT + r")第\s*(\d+)\s*條(?:之\s*(\d+))?")
CLAUSE_77_RE = re.compile(r"訴願法第\s*77\s*條第\s*(\d+)\s*款")

SOURCE_TEMPLATES = {
    "NTPC": "新北市政府訴願決定書查詢系統 https://web.law.ntpc.gov.tw/Su_search01.aspx",
    "TC": "臺中市政府訴願決定書全文檢索 https://appeal.taichung.gov.tw/Home/FN0601",
    "TY": "桃園市政府訴願便民服務系統 https://ctaw.tycg.gov.tw/ViewDecision/Search",
}
# eno(新北)/ID(桃園)是查詢系統內部序號,不隨案號可推導,本機無法重建可用深連結,
# 只能用同一個查詢入口的搜尋頁充當保底 source_url(仍優於留空);臺中的 URL 全靠案號即可重建。
SOURCE_URL_BUILDERS = {
    "TC": lambda digits, ymd: f"https://appeal.taichung.gov.tw/Home/FN0602?case_no={digits}",
    "NTPC": lambda digits, ymd: "https://web.law.ntpc.gov.tw/Su_search01.aspx",
    "TY": lambda digits, ymd: "https://ctaw.tycg.gov.tw/ViewDecision/Search",
}

DECISION_DATE_RE = re.compile(r"中華民國\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日")


class SectionSplitError(ValueError):
    """找不到可靠的主文/事實/理由邊界,拒絕硬吞成空字串。"""


def parse_filename(pdf_name: str) -> tuple[dict | None, str | None]:
    """回傳 (info, error)。info 含 year/group/case_no/source_file/case_type/is_official。"""
    stem = pdf_name[:-4] if pdf_name.lower().endswith(".pdf") else pdf_name
    m = FILENAME_RE.match(stem)
    if not m:
        return None, f"檔名不符「日期_案號_案由[_官方]」格式: {pdf_name}"
    case_no_token = m.group("case_no")
    cm = CASE_NO_RE.match(case_no_token)
    if not cm:
        return None, f"案號欄位不是「英文字母前綴+數字」: {case_no_token}"
    group = cm.group("letters").upper() or "NTPC"
    digits = cm.group("digits")
    return {
        "year": m.group("year"),
        "group": group,
        "case_no": case_no_token,
        "source_file": f"{group}-{digits}",
        "digits": digits,
        "case_type": m.group("case_type"),
        "is_official": bool(m.group("official")),
    }, None


def bucket_for_case_type(case_type: str) -> str:
    """既有語料驗證過:只有完全相等才落自己的桶,自由文字變體一律其他案型。"""
    return case_type if case_type in BUCKETS else "其他案型"


def get_case_body(raw_text: str) -> str:
    """標籤式版型取最後一個「全文：」/「內文：」標籤之後的內容;直式版型原樣使用。"""
    matches = list(BODY_LABEL_RE.finditer(raw_text))
    if matches:
        return raw_text[matches[-1].end():].strip()
    return raw_text.strip()


def split_main_sections(body: str) -> dict:
    """回傳 {"主文": str, "事實": str, "理由": str}(事實可能是空字串)。"""
    anchor = DECISION_ANCHOR_RE.search(body)
    # 極少數案件漏寫「決定如下」(如 ty_facts_only 以外的個案),退而求其次直接在開頭附近找主文標題
    search_start = anchor.end() if anchor else 0
    main_m = MAIN_RE.search(body, search_start, search_start + 300)
    if not main_m:
        raise SectionSplitError("找不到「決定如下」錨點與「主文」標題,無法定位主文起點")

    facts_m = FACTS_RE.search(body, main_m.end())
    reason_m = REASON_RE.search(body, main_m.end())
    if not facts_m and not reason_m:
        raise SectionSplitError("找不到「事實」或「理由」標題")

    facts_first = bool(facts_m) and (not reason_m or facts_m.start() < reason_m.start())

    if facts_first:
        main_text = body[main_m.end():facts_m.start()].strip()
    else:
        main_text = body[main_m.end():reason_m.start()].strip()
    if len(main_text) > MAIN_TEXT_MAX_LEN:
        raise SectionSplitError(f"主文內容異常長({len(main_text)}字),可能誤配標題位置")

    if not facts_first:
        sig = SIGNATURE_RE.search(body, reason_m.end())
        if not sig:
            raise SectionSplitError("找不到簽署段落,無法界定理由結尾")
        reason_text = body[reason_m.end():sig.start()].strip()
        if not reason_text:
            raise SectionSplitError("理由段落為空")
        return {"主文": main_text, "事實": "", "理由": reason_text}

    reason_after_facts = REASON_RE.search(body, facts_m.end())
    if reason_after_facts:
        facts_text = body[facts_m.end():reason_after_facts.start()].strip()
        sig = SIGNATURE_RE.search(body, reason_after_facts.end())
        if not sig:
            raise SectionSplitError("找不到簽署段落,無法界定理由結尾")
        reason_text = body[reason_after_facts.end():sig.start()].strip()
        if not reason_text:
            raise SectionSplitError("理由段落為空")
        return {"主文": main_text, "事實": facts_text, "理由": reason_text}

    # 只找到「事實」,沒有「理由」:段落含結論語才判定是理由誤標事實,否則視為缺理由標題
    sig = SIGNATURE_RE.search(body, facts_m.end())
    block_end = sig.start() if sig else len(body)
    block = body[facts_m.end():block_end].strip()
    if not sig or not CONCLUSION_RE.search(block):
        raise SectionSplitError("有「事實」卻找不到「理由」,且事實段落沒有結論語,無法判定內容歸屬")
    return {"主文": main_text, "事實": "", "理由": block}


def _inside_open_quote(text: str, pos: int) -> bool:
    """引號內的項次是轉錄法條自己的款次(如訴願法第56條九款),不是本文的頂層論理項次。"""
    return (text[:pos].count("「") - text[:pos].count("」")) % 2 == 1


def split_items(section_text: str) -> list[str]:
    """依「一、二、三…」等頂層項次切段;項次前的無編號引言自成一段。
    引號「」內的項次記號(轉錄自法條原文,如「…一、…二、…」)不算頂層項次,不切。"""
    text = section_text.strip()
    if not text:
        return []
    marks = [m for m in ITEM_SPLIT_RE.finditer(text) if not _inside_open_quote(text, m.start())]
    if not marks:
        return [text]
    items = []
    if marks[0].start() > 0:
        items.append(text[:marks[0].start()].strip())
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        piece = text[mark.start():end].strip()
        if piece:
            items.append(piece)
    return [p for p in items if p]


def classify_role(section: str, item_text: str, is_last: bool) -> str:
    """見模組 docstring 的啟發式清單,依序判定、先中先得。"""
    if section == "事實":
        return "事實敘述"

    stripped = LEADING_ENUM_RE.sub("", item_text).strip()

    if is_last and CONCLUSION_RE.search(item_text):
        return "結語"
    if CITATION_OPEN_RE.match(stripped):
        return "法規引述"
    if len(ARTICLE_RE.findall(item_text)) >= 2 and not CASE_SPECIFIC_RE.search(item_text):
        return "法規引述"
    if REBUTTAL_RE.search(item_text):
        return "其他項次"
    if CONTINUATION_OPEN_RE.match(stripped):
        return "接續段"
    if CASE_SPECIFIC_RE.search(item_text):
        return "本案論理"
    if LEADING_ENUM_RE.match(item_text):
        return "其他項次"
    return "本案論理"


def extract_related_laws(text: str) -> str:
    seen = []
    last_law = None
    for law_raw, article, zhi in RELATED_LAW_RE.findall(text):
        law = last_law if law_raw in _SAME_LAW_TOKENS else law_raw
        if law_raw not in _SAME_LAW_TOKENS:
            last_law = law
        if not law:
            continue
        label = f"{law} 第{article}條" + (f"之{zhi}" if zhi else "")
        if label not in seen:
            seen.append(label)
    return "；".join(seen)


def extract_clause(text: str) -> str:
    m = CLAUSE_77_RE.search(text)
    if m:
        return f"§77({m.group(1)})"
    if re.search(r"訴願法第\s*81\s*條", text) or "另為適法之處分" in text:
        return "§81"
    if re.search(r"訴願法第\s*79\s*條", text):
        return "§79"
    return ""


def extract_result(main_text: str) -> str:
    # 值域比照既有語料:{駁回,不受理,撤銷,部分,其他};「部分」要先判,免得複合主文被
    # 其中一個子句的「不受理」或「駁回」字樣搶先歸類
    if "部分" in main_text:
        return "部分"
    if "不受理" in main_text:
        return "不受理"
    if "駁回" in main_text:
        return "駁回"
    if "撤銷" in main_text:
        return "撤銷"
    return "其他"


def extract_decision_date(raw_text: str) -> tuple[str, str, str] | None:
    matches = list(DECISION_DATE_RE.finditer(raw_text))
    if not matches:
        return None
    y, mo, d = matches[-1].groups()
    return y, mo, d


def build_source_fields(group: str, digits: str, raw_text: str) -> tuple[str, str]:
    source = SOURCE_TEMPLATES.get(group, "")
    date = extract_decision_date(raw_text)
    ymd = f"{date[0]}{int(date[1]):02d}{int(date[2]):02d}" if date else ""
    builder = SOURCE_URL_BUILDERS.get(group)
    source_url = builder(digits, ymd) if builder else ""
    return source, source_url


def parse_pdf_text(info: dict, raw_text: str) -> tuple[list[dict], str | None]:
    """單一檔案的完整解析:檔名 info + 全文 -> (rows, error)。失敗回傳 ([], reason)。"""
    try:
        body = get_case_body(raw_text)
        sections = split_main_sections(body)
    except SectionSplitError as exc:
        return [], str(exc)

    main_text = sections["主文"]
    reason_text = sections["理由"]
    has_substantive = any(
        classify_role("理由", item, is_last=(i == len(items) - 1)) == "本案論理"
        for items in [split_items(reason_text)]
        for i, item in enumerate(items)
    )
    related_laws = extract_related_laws(body)
    clause = extract_clause(reason_text)
    result = extract_result(main_text)
    source, source_url = build_source_fields(info["group"], info["digits"], raw_text)

    rows = []
    for section_name, section_text in (("事實", sections["事實"]), ("理由", reason_text)):
        items = split_items(section_text)
        for seq, item in enumerate(items):
            role = classify_role(section_name, item, is_last=(seq == len(items) - 1))
            chunk_id = f"{info['source_file']}-{section_name}-{seq:03d}"
            metadata = {
                "source_file": info["source_file"],
                "year": info["year"],
                "case_type": info["case_type"],
                "clause": clause,
                "result": result,
                "case_no": info["case_no"],
                "doc_no": "",
                "related_laws": related_laws,
                "doc_type": "訴願決定書",
                "source": source,
                "source_url": source_url,
                "有實體論理": "true" if has_substantive else "false",
                "section": section_name,
                "paragraph_role": role,
            }
            rows.append({
                "片段名": chunk_id,
                "來源檔": info["source_file"],
                "文件類型": "訴願決定書",
                "段落角色": role,
                "內容": item,
                "metadata": metadata,
            })
    if not rows:
        return [], "切出主文/事實/理由後沒有任何項次可產生 chunk"
    return rows, None


def extract_text(pdf_path: Path) -> str:
    with fitz.open(pdf_path) as doc:
        text = "\n".join(page.get_text() for page in doc)
    return unicodedata.normalize("NFC", text)


def load_official_case_nos() -> set[str]:
    if not CASE_CHUNKS_PATH.exists():
        return set()
    case_nos = set()
    with CASE_CHUNKS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            case_nos.add(row["metadata"]["case_no"])
    return case_nos


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", type=Path, default=MISSING_LIST_PATH)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_PDF_DIR)
    parser.add_argument("--output-dir", type=Path, default=CRAWL_OUTPUT_DIR)
    args = parser.parse_args()

    filenames = [
        Path(line.strip()).name
        for line in args.list.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    official_case_nos = load_official_case_nos()

    by_bucket: dict[str, list[dict]] = {}
    skipped_official = 0
    failures: list[tuple[str, str]] = []
    role_counter: Counter = Counter()
    result_counter: Counter = Counter()
    related_laws_nonempty = 0
    total_rows = 0
    success = 0

    for name in filenames:
        info, err = parse_filename(name)
        if info is None:
            failures.append((name, err))
            continue
        if info["is_official"] and info["case_no"] in official_case_nos:
            skipped_official += 1
            continue

        pdf_path = args.source_dir / name
        if not pdf_path.exists():
            failures.append((name, f"找不到來源檔: {pdf_path}"))
            continue
        raw_text = extract_text(pdf_path)
        rows, err = parse_pdf_text(info, raw_text)
        if err:
            failures.append((name, err))
            continue

        bucket = bucket_for_case_type(info["case_type"])
        by_bucket.setdefault(bucket, []).extend(rows)
        success += 1
        total_rows += len(rows)
        for row in rows:
            role_counter[row["段落角色"]] += 1
            if row["metadata"]["related_laws"]:
                related_laws_nonempty += 1
        result_counter[rows[0]["metadata"]["result"]] += 1

    for bucket, rows in by_bucket.items():
        write_jsonl(args.output_dir / f"決定書-{bucket}.jsonl", rows)

    print(f"=== 解析結果: 成功 {success}/{len(filenames)} 檔(官方重複跳過 {skipped_official}) ===")
    print(f"chunk 總數: {total_rows}")
    print(f"role 分布: {dict(role_counter)}")
    print(f"result 分布: {dict(result_counter)}")
    print(f"related_laws 非空 chunk 數: {related_laws_nonempty}/{total_rows}")
    print(f"各桶案數: {dict((b, len({r['來源檔'] for r in rows})) for b, rows in by_bucket.items())}")
    print(f"失敗 {len(failures)} 件:")
    for name, reason in failures:
        print(f"  - {name}: {reason}")


if __name__ == "__main__":
    main()
