"""建立 S3 Vectors bucket/index、IAM role appeal-kb-role、Bedrock Knowledge Base ×2 + data source。
可重跑:每項資源建立前先檢查是否已存在,存在則 [SKIP]。
執行者 WSParticipantRole 可能沒有 iam:CreateRole 權限:遇 AccessDenied 印替代方案後 exit(1),不吞錯。
"""
import json
import sys
import time

import boto3
from botocore.exceptions import ClientError

from config import (
    ACCOUNT_ID,
    EMBEDDING_MODEL_ARN,
    EMBEDDING_DIMENSION,
    IAM_ROLE_NAME,
    INDEX_CASE,
    INDEX_LAW,
    KB_CASE_NAME,
    KB_LAW_NAME,
    REGION,
    VECTOR_BUCKET,
    VECTOR_DIMENSION,
    VECTOR_DISTANCE_METRIC,
    load_resources,
    save_resources,
)

KB_ACTIVE_TIMEOUT_SEC = 600
KB_POLL_INTERVAL_SEC = 10

ALREADY_EXISTS_CODES = {
    "ConflictException",
    "EntityAlreadyExists",
    "ResourceInUseException",
    "VectorBucketAlreadyExists",
    "IndexAlreadyExists",
}


def is_already_exists(e: ClientError) -> bool:
    code = e.response.get("Error", {}).get("Code", "")
    if code in ALREADY_EXISTS_CODES:
        return True
    return "already exist" in str(e).lower()


def exit_on_access_denied(e: ClientError, action: str):
    code = e.response.get("Error", {}).get("Code", "")
    if code == "AccessDenied":
        print(
            "[ERROR] IAM 權限不足,無法執行: " + action + "\n"
            "  執行身分 WSParticipantRole 可能沒有 iam:CreateRole / iam:PutRolePolicy 權限。\n"
            "  替代方案:\n"
            "  1) 請有權限者於 AWS Console 手動建立 IAM role「" + IAM_ROLE_NAME + "」,\n"
            "     trust policy 允許 bedrock.amazonaws.com AssumeRole,並附加 inline policy 允許\n"
            "     s3vectors:* (resource 限定 vector bucket " + VECTOR_BUCKET + ") 與\n"
            "     bedrock:InvokeModel (resource 限定 " + EMBEDDING_MODEL_ARN + "),\n"
            "     再把 role ARN 寫入 aws_setup/resources.json 的 \"role_arn\" 欄位後重跑本腳本。\n"
            "  2) 或改用既有已具備上述權限的 role,將其 ARN 寫入 resources.json 的 \"role_arn\" 欄位後重跑。\n"
            f"  原始錯誤: {e}",
            file=sys.stderr,
        )
        sys.exit(1)
    raise e


def ensure_vector_bucket(s3vectors):
    try:
        s3vectors.create_vector_bucket(vectorBucketName=VECTOR_BUCKET)
        print(f"[CREATE] vector bucket 建立完成: {VECTOR_BUCKET}")
    except ClientError as e:
        if is_already_exists(e):
            print(f"[SKIP] vector bucket 已存在: {VECTOR_BUCKET}")
        else:
            raise


def ensure_index(s3vectors, index_name: str) -> str:
    """建立 index(若不存在),回傳 index ARN。"""
    try:
        s3vectors.create_index(
            vectorBucketName=VECTOR_BUCKET,
            indexName=index_name,
            dataType="float32",
            dimension=VECTOR_DIMENSION,
            distanceMetric=VECTOR_DISTANCE_METRIC,
        )
        print(f"[CREATE] index 建立完成: {index_name}")
    except ClientError as e:
        if is_already_exists(e):
            print(f"[SKIP] index 已存在: {index_name}")
        else:
            raise

    resp = s3vectors.get_index(vectorBucketName=VECTOR_BUCKET, indexName=index_name)
    return resp["index"]["indexArn"]


def ensure_role(iam) -> str:
    resources = load_resources()
    existing_arn = resources.get("role_arn")
    if existing_arn:
        print(f"[SKIP] resources.json 已指定 role_arn,沿用: {existing_arn}")
        return existing_arn

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    created = False
    try:
        resp = iam.create_role(
            RoleName=IAM_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Bedrock Knowledge Base role for appeal-ai",
        )
        role_arn = resp["Role"]["Arn"]
        created = True
        print(f"[CREATE] IAM role 建立完成: {IAM_ROLE_NAME}")
    except ClientError as e:
        if is_already_exists(e):
            role_arn = iam.get_role(RoleName=IAM_ROLE_NAME)["Role"]["Arn"]
            print(f"[SKIP] IAM role 已存在: {IAM_ROLE_NAME}")
        else:
            exit_on_access_denied(e, f"iam:CreateRole ({IAM_ROLE_NAME})")
            raise

    inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "s3vectors:*",
                "Resource": [
                    f"arn:aws:s3vectors:{REGION}:{ACCOUNT_ID}:bucket/{VECTOR_BUCKET}",
                    f"arn:aws:s3vectors:{REGION}:{ACCOUNT_ID}:bucket/{VECTOR_BUCKET}/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": "bedrock:InvokeModel",
                "Resource": EMBEDDING_MODEL_ARN,
            },
        ],
    }
    try:
        iam.put_role_policy(
            RoleName=IAM_ROLE_NAME,
            PolicyName="appeal-kb-role-policy",
            PolicyDocument=json.dumps(inline_policy),
        )
        print("[CREATE] IAM inline policy 附加完成: appeal-kb-role-policy")
    except ClientError as e:
        exit_on_access_denied(e, "iam:PutRolePolicy (appeal-kb-role-policy)")
        raise

    if created:
        print("等待 IAM role 傳播(10 秒)...")
        time.sleep(10)

    return role_arn


def find_kb_id_by_name(bedrock_agent, name: str):
    resp = bedrock_agent.list_knowledge_bases()
    for kb in resp.get("knowledgeBaseSummaries", []):
        if kb.get("name") == name:
            return kb.get("knowledgeBaseId")
    return None


def wait_kb_active(bedrock_agent, kb_id: str, name: str):
    waited = 0
    while waited <= KB_ACTIVE_TIMEOUT_SEC:
        status = bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]["status"]
        if status == "ACTIVE":
            print(f"[ACTIVE] knowledge base {name} 已就緒: {kb_id}")
            return
        if status == "FAILED":
            raise RuntimeError(f"knowledge base {name} 建立失敗(status=FAILED): {kb_id}")
        time.sleep(KB_POLL_INTERVAL_SEC)
        waited += KB_POLL_INTERVAL_SEC
    raise TimeoutError(f"knowledge base {name} 逾時未進入 ACTIVE: {kb_id}")


def ensure_knowledge_base(bedrock_agent, name: str, role_arn: str, index_arn: str) -> str:
    kb_id = find_kb_id_by_name(bedrock_agent, name)
    if kb_id:
        print(f"[SKIP] knowledge base 已存在: {name} ({kb_id})")
        wait_kb_active(bedrock_agent, kb_id, name)
        return kb_id

    resp = bedrock_agent.create_knowledge_base(
        name=name,
        roleArn=role_arn,
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": EMBEDDING_MODEL_ARN,
                # cohere.embed-multilingual-v3 固定 1024 維,不支援 embeddingModelConfiguration
            },
        },
        storageConfiguration={
            "type": "S3_VECTORS",
            "s3VectorsConfiguration": {"indexArn": index_arn},
        },
    )
    kb_id = resp["knowledgeBase"]["knowledgeBaseId"]
    print(f"[CREATE] knowledge base 建立中: {name} ({kb_id})")
    wait_kb_active(bedrock_agent, kb_id, name)
    return kb_id


def find_data_source_id(bedrock_agent, kb_id: str, name: str):
    resp = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)
    for ds in resp.get("dataSourceSummaries", []):
        if ds.get("name") == name:
            return ds.get("dataSourceId")
    return None


def ensure_data_source(bedrock_agent, kb_id: str, kb_name: str) -> str:
    ds_name = f"{kb_name}-ds"
    ds_id = find_data_source_id(bedrock_agent, kb_id, ds_name)
    if ds_id:
        print(f"[SKIP] data source 已存在: {ds_name} ({ds_id})")
        return ds_id

    resp = bedrock_agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=ds_name,
        dataSourceConfiguration={"type": "CUSTOM"},
    )
    ds_id = resp["dataSource"]["dataSourceId"]
    print(f"[CREATE] data source 建立完成: {ds_name} ({ds_id})")
    return ds_id


def main():
    s3vectors = boto3.client("s3vectors", region_name=REGION)
    iam = boto3.client("iam")
    bedrock_agent = boto3.client("bedrock-agent", region_name=REGION)

    ensure_vector_bucket(s3vectors)
    index_law_arn = ensure_index(s3vectors, INDEX_LAW)
    index_case_arn = ensure_index(s3vectors, INDEX_CASE)

    role_arn = ensure_role(iam)

    kb_law_id = ensure_knowledge_base(bedrock_agent, KB_LAW_NAME, role_arn, index_law_arn)
    kb_case_id = ensure_knowledge_base(bedrock_agent, KB_CASE_NAME, role_arn, index_case_arn)

    ds_law_id = ensure_data_source(bedrock_agent, kb_law_id, KB_LAW_NAME)
    ds_case_id = ensure_data_source(bedrock_agent, kb_case_id, KB_CASE_NAME)

    save_resources(
        {
            "vector_bucket": VECTOR_BUCKET,
            "index_law_arn": index_law_arn,
            "index_case_arn": index_case_arn,
            "role_arn": role_arn,
            "kb_law_id": kb_law_id,
            "kb_case_id": kb_case_id,
            "data_source_law_id": ds_law_id,
            "data_source_case_id": ds_case_id,
        }
    )

    print(
        "[DONE] "
        f"vector_bucket={VECTOR_BUCKET} kb_law_id={kb_law_id} kb_case_id={kb_case_id} "
        f"data_source_law_id={ds_law_id} data_source_case_id={ds_case_id}"
    )


if __name__ == "__main__":
    try:
        main()
    except ClientError as e:
        print(f"[ERROR] AWS 呼叫失敗: {e}", file=sys.stderr)
        sys.exit(1)
