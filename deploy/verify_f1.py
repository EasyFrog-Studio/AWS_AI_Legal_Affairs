"""驗收腳本:對已部署站台實測「上傳四份 PDF → F1 結構化擷取」。

用法:
    python deploy/verify_f1.py --base-url http://<alb-dns-name> --api-key 0000 \
        [--case-dir data_show/test_cases/example1] [--timeout 900]

只用標準函式庫(urllib),不新增第三方相依套件(backend/requirements.txt 沒有 requests)。
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

sys.stdout.reconfigure(encoding="utf-8")  # Windows 主控台預設編碼會把中文報告印成亂碼

# multipart 欄位名對照 POST /api/cases 的參數名
SLOT_FIELD = {
    "appeal": "appeal_file",
    "service": "service_file",
    "disposition": "disposition_file",
    "answer": "answer_file",
}
# 測資檔名前綴對照四個文件槽(依 data_show/test_cases/README.md)
SLOT_PREFIX = {
    "appeal": "01_",
    "disposition": "02_",
    "service": "03_",
    "answer": "04_",
}
# service(送達證書)選填,其餘三槽必填——機關答辯是另一造主張,缺了只聽得到一方
REQUIRED_SLOTS = ("appeal", "disposition", "answer")
ALL_SLOTS = ("appeal", "service", "disposition", "answer")


def find_case_dir(repo_root: Path) -> Path:
    """挑 data_show/test_cases/ 底下第一個湊得齊必要文件的資料夾。"""
    base = repo_root / "data_show" / "test_cases"
    for candidate in sorted(p for p in base.iterdir() if p.is_dir()):
        if all(_find_slot_file(candidate, slot) for slot in REQUIRED_SLOTS):
            return candidate
    raise SystemExit(f"[fatal] {base} 底下找不到任何一組湊得齊必要文件的測資資料夾")


def _find_slot_file(case_dir: Path, slot: str) -> Optional[Path]:
    prefix = SLOT_PREFIX[slot]
    matches = sorted(case_dir.glob(f"{prefix}*.pdf"))
    return matches[0] if matches else None


def resolve_slots(case_dir: Path) -> dict[str, Path]:
    slots: dict[str, Path] = {}
    for slot in ALL_SLOTS:
        path = _find_slot_file(case_dir, slot)
        if path is None and slot in REQUIRED_SLOTS:
            raise SystemExit(
                f"[fatal] {case_dir} 缺少必要文件槽「{slot}」"
                f"(找不到符合 {SLOT_PREFIX[slot]}*.pdf 的檔案)"
            )
        if path is not None:
            slots[slot] = path
    return slots


def build_multipart(files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for field, path in files.items():
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode("utf-8")
        parts.append(header + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def http_request(
    method: str, url: str, api_key: Optional[str] = None, body: Optional[bytes] = None,
    content_type: Optional[str] = None,
) -> tuple[int, bytes]:
    """回傳 (status_code, body_bytes);非 2xx 不丟例外,呼叫端自己判斷。"""
    headers = {}
    if api_key is not None:
        headers["X-API-Key"] = api_key
    if content_type is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def check_health(base_url: str) -> None:
    # 這是唯一不需要 X-API-Key 的端點
    url = f"{base_url}/api/health"
    try:
        status, body = http_request("GET", url)
    except urllib.error.URLError as exc:
        print(f"[fatal] 健康檢查失敗:無法連上 {url}({exc})")
        sys.exit(1)
    if status != 200:
        print(f"[fatal] 健康檢查失敗:HTTP {status}\n{body.decode('utf-8', 'replace')}")
        sys.exit(1)
    data = json.loads(body)
    print(f"[health] status={data.get('status')} provider={data.get('provider')}")


def create_case(base_url: str, api_key: str, slots: dict[str, Path]) -> str:
    for slot in ALL_SLOTS:
        path = slots.get(slot)
        print(f"[case] 文件槽 {slot} ({SLOT_FIELD[slot]}) = {path if path else '(未提供,選填)'}")
    files = {SLOT_FIELD[slot]: path for slot, path in slots.items()}
    body, content_type = build_multipart(files)
    status, resp_body = http_request(
        "POST", f"{base_url}/api/cases", api_key=api_key, body=body, content_type=content_type,
    )
    if not (200 <= status < 300):
        print(f"[fatal] 建案失敗:HTTP {status}\n{resp_body.decode('utf-8', 'replace')}")
        sys.exit(1)
    data = json.loads(resp_body)
    case_id = data["case_id"]
    print(f"[case] 建案成功 case_id={case_id}")
    return case_id


def poll_until_f1(base_url: str, api_key: str, case_id: str, timeout: float) -> dict:
    """輪詢 GET /api/cases/{id},current_stage 越過 "f1" 代表 f1 已有結果;f1 與 current_stage 是同一次寫入。"""
    deadline = time.monotonic() + timeout
    last_case: dict = {}
    while True:
        status_code, body = http_request("GET", f"{base_url}/api/cases/{case_id}", api_key=api_key)
        if status_code != 200:
            print(f"[fatal] 輪詢失敗:HTTP {status_code}\n{body.decode('utf-8', 'replace')}")
            sys.exit(1)
        last_case = json.loads(body)
        stage = last_case.get("current_stage")
        case_status = last_case.get("status")
        elapsed = timeout - (deadline - time.monotonic())
        print(f"[poll] elapsed={elapsed:.0f}s status={case_status} current_stage={stage}")

        if case_status == "error":
            print(f"[fatal] 案件處理失敗:status=error error={last_case.get('error')}")
            sys.exit(1)
        if stage is not None and stage != "f1":
            return last_case
        if time.monotonic() >= deadline:
            print(
                f"[fatal] 逾時({timeout}s):最後狀態 status={case_status} "
                f"current_stage={stage}"
            )
            sys.exit(1)
        time.sleep(5)


def report_f1(case: dict) -> None:
    f1 = case.get("f1")
    if not f1:
        print("[fatal] current_stage 已越過 f1 但回應裡沒有 f1 欄位")
        sys.exit(1)

    total = 0
    filled = 0
    unspecified = 0
    for key, value in f1.items():
        total += 1
        display = "、".join(str(v) for v in value) if isinstance(value, list) else str(value)
        is_empty = (isinstance(value, list) and len(value) == 0) or (not isinstance(value, list) and value == "")
        is_unspecified = value == "未載明" or (isinstance(value, list) and value == ["未載明"])
        if not is_empty:
            filled += 1
        if is_unspecified:
            unspecified += 1
        if len(display) > 80:
            display = display[:80] + "..."
        print(f"{key}: {display}")

    print(f"\n[summary] 總欄位數={total} 有值={filled} 未載明={unspecified}")


def main() -> None:
    parser = argparse.ArgumentParser(description="驗收:上傳四份 PDF → F1 結構化擷取")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--case-dir")
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    repo_root = Path(__file__).resolve().parent.parent

    check_health(base_url)

    case_dir = Path(args.case_dir) if args.case_dir else find_case_dir(repo_root)
    if not case_dir.is_dir():
        print(f"[fatal] --case-dir 不是資料夾:{case_dir}")
        sys.exit(1)
    print(f"[case] 使用測資資料夾:{case_dir}")
    slots = resolve_slots(case_dir)

    case_id = create_case(base_url, args.api_key, slots)
    case = poll_until_f1(base_url, args.api_key, case_id, args.timeout)
    report_f1(case)


if __name__ == "__main__":
    main()
