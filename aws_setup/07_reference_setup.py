"""建立參考見解三類(司法院釋字/行政函釋/行政法院裁判)的 S3 Vectors index、Bedrock KB、data source、DynamoDB 表。
只建空殼,不灌任何資料;灌資料是另一支腳本的事。
role_arn 與 vector bucket 沿用 03_vectors_kb.py 已建立的既有資源,本腳本不建立。
可重跑:每項資源建立前先檢查是否已存在,存在則 [SKIP]。
"""
import sys
import time

import boto3
from botocore.exceptions import ClientError

from config import (
    REFERENCE_DDB_PK,
    REFERENCE_KINDS,
    REGION,
    VECTOR_BUCKET,
    VECTOR_DIMENSION,
    VECTOR_DISTANCE_METRIC,
    EMBEDDING_MODEL_ARN,
    load_resources,
    save_resources,
)

KB_ACTIVE_TIMEOUT_SEC = 600
KB_POLL_INTERVAL_SEC = 10

# doc_kind -> resources.json key 用的 slug,新增一類同時要改 config.REFERENCE_KINDS 與這裡
DOC_KIND_SLUGS = {
    "司法院釋字": "interpretation",
    "行政函釋": "ruling",
    "行政法院裁判": "judgment",
}

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
                # titan-embed-text-v2 固定 1024 維,不支援 embeddingModelConfiguration
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


def ensure_table(ddb, table_name: str, pk_name: str):
    try:
        ddb.describe_table(TableName=table_name)
        print(f"[SKIP] 表已存在: {table_name}")
        return
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    ddb.create_table(
        TableName=table_name,
        AttributeDefinitions=[{"AttributeName": pk_name, "AttributeType": "S"}],
        KeySchema=[{"AttributeName": pk_name, "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )
    waiter = ddb.get_waiter("table_exists")
    waiter.wait(TableName=table_name)
    print(f"[CREATE] 表建立完成: {table_name}")


def main():
    resources = load_resources()
    role_arn = resources.get("role_arn")
    if not role_arn:
        print(
            "[ERROR] resources.json 缺少 role_arn,請先跑 03_vectors_kb.py 建立 KB role 後再重跑本腳本。",
            file=sys.stderr,
        )
        sys.exit(1)

    s3vectors = boto3.client("s3vectors", region_name=REGION)
    bedrock_agent = boto3.client("bedrock-agent", region_name=REGION)
    ddb = boto3.client("dynamodb", region_name=REGION)

    updates = {}
    kb_ids = {}
    for doc_kind, kb_name, index_name, ddb_table in REFERENCE_KINDS:
        slug = DOC_KIND_SLUGS[doc_kind]

        index_arn = ensure_index(s3vectors, index_name)
        kb_id = ensure_knowledge_base(bedrock_agent, kb_name, role_arn, index_arn)
        ds_id = ensure_data_source(bedrock_agent, kb_id, kb_name)
        ensure_table(ddb, ddb_table, REFERENCE_DDB_PK)

        updates[f"index_{slug}_arn"] = index_arn
        updates[f"kb_{slug}_id"] = kb_id
        updates[f"data_source_{slug}_id"] = ds_id
        kb_ids[doc_kind] = kb_id

    save_resources(updates)

    print(
        "[DONE] "
        f"kb_interpretation_id={kb_ids['司法院釋字']} "
        f"kb_ruling_id={kb_ids['行政函釋']} "
        f"kb_judgment_id={kb_ids['行政法院裁判']}"
    )


if __name__ == "__main__":
    try:
        main()
    except ClientError as e:
        print(f"[ERROR] AWS 呼叫失敗: {e}", file=sys.stderr)
        sys.exit(1)
