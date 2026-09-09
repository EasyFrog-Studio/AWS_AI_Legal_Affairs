"""建立 S3 bucket 並上傳 data/ PDF 與 data/output/markdown/ 到 S3。
可重跑:bucket 已存在跳過建立;物件已存在(依 key)跳過上傳。
"""
import re
import sys

import boto3
from botocore.exceptions import ClientError

from config import DATASET_DIR, MARKDOWN_DIR, REGION, S3_BUCKET

PDF_COPY_SUFFIX_RE = re.compile(r"\.pdf 的副本\.pdf$")
HOLDOUT_YEARS = frozenset({"114"})  # 留出法測試集,連原始 PDF 都不上雲,免得日後有人從 raw/ 建索引


def clean_filename(name: str) -> str:
    """去除資料集檔名的 '.pdf 的副本.pdf' 後綴,回傳乾淨檔名(含 .pdf)。"""
    return PDF_COPY_SUFFIX_RE.sub(".pdf", name)


def ensure_bucket(s3):
    try:
        s3.head_bucket(Bucket=S3_BUCKET)
        print(f"[SKIP] bucket 已存在: {S3_BUCKET}")
        return
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code not in ("404", "NoSuchBucket"):
            raise

    create_kwargs = {"Bucket": S3_BUCKET}
    if REGION != "us-east-1":
        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": REGION}
    s3.create_bucket(**create_kwargs)
    print(f"[CREATE] bucket 建立完成: {S3_BUCKET}")


def object_exists(s3, key: str) -> bool:
    try:
        s3.head_object(Bucket=S3_BUCKET, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def upload_file(s3, local_path, key: str, counters: dict):
    if object_exists(s3, key):
        counters["skip"] += 1
        return
    s3.upload_file(str(local_path), S3_BUCKET, key)
    counters["upload"] += 1


def upload_raw_pdfs(s3, counters: dict):
    if not DATASET_DIR.exists():
        print(f"[SKIP] 找不到資料集目錄: {DATASET_DIR}")
        return
    holdout_dirs = {f"{year}年" for year in HOLDOUT_YEARS}
    for pdf_path in DATASET_DIR.rglob("*.pdf"):
        rel_dir_parts = pdf_path.parent.relative_to(DATASET_DIR).parts  # 類別資料夾(可能含年度子資料夾)
        if holdout_dirs & set(rel_dir_parts):
            counters["holdout"] += 1
            continue
        clean_name = clean_filename(pdf_path.name)
        key_parts = ["raw", *rel_dir_parts, clean_name]
        key = "/".join(key_parts)
        upload_file(s3, pdf_path, key, counters)


def upload_markdown(s3, counters: dict):
    if not MARKDOWN_DIR.exists():
        print(f"[SKIP] 找不到 markdown 目錄: {MARKDOWN_DIR}")
        return
    for md_path in MARKDOWN_DIR.rglob("*.md"):
        rel_parts = md_path.relative_to(MARKDOWN_DIR).parts
        key = "/".join(["markdown", *rel_parts])
        upload_file(s3, md_path, key, counters)


def main():
    s3 = boto3.client("s3", region_name=REGION)
    ensure_bucket(s3)

    counters = {"upload": 0, "skip": 0, "holdout": 0}
    upload_raw_pdfs(s3, counters)
    upload_markdown(s3, counters)

    print(
        f"[DONE] bucket={S3_BUCKET} 上傳={counters['upload']} 跳過(已存在)={counters['skip']}"
        f" 排除(留出年度)={counters['holdout']}"
    )


if __name__ == "__main__":
    try:
        main()
    except ClientError as e:
        print(f"[ERROR] AWS 呼叫失敗: {e}", file=sys.stderr)
        sys.exit(1)
