"""建立 App Runner 服務指向 ECR image(冪等);需 access role(拉 ECR)與 instance role(呼叫 Bedrock/DynamoDB/S3),無 iam:CreateRole 時印出手動步驟後 exit(1)。"""
import json
import sys
import time
from pathlib import Path

import boto3

REGION = "us-west-2"
ACCOUNT = "047877300727"
SERVICE = "appeal-ai"
IMAGE = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/appeal-ai:latest"
ACCESS_ROLE = "appeal-apprunner-ecr-access"
INSTANCE_ROLE = "appeal-apprunner-instance"

iam = boto3.client("iam")
apprunner = boto3.client("apprunner", region_name=REGION)


def ensure_role(name: str, service_principal: str, policy: dict) -> str:
    try:
        return iam.get_role(RoleName=name)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        pass
    trust = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": service_principal},
                       "Action": "sts:AssumeRole"}],
    }
    arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
    iam.put_role_policy(RoleName=name, PolicyName=f"{name}-policy", PolicyDocument=json.dumps(policy))
    time.sleep(10)  # IAM 傳播
    return arn


def load_env() -> dict:
    env_path = Path(__file__).resolve().parents[1] / "docker" / ".env"
    env = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env["AI_PROVIDER"] = "aws"  # 上雲一律 aws 模式
    env.pop("CASE_STORE", None)
    return {k: v for k, v in env.items() if v}


def main() -> None:
    try:
        access_arn = ensure_role(ACCESS_ROLE, "build.apprunner.amazonaws.com", {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Action": [
                "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage",
                "ecr:BatchCheckLayerAvailability", "ecr:GetAuthorizationToken"],
                "Resource": "*"}],
        })
        instance_arn = ensure_role(INSTANCE_ROLE, "tasks.apprunner.amazonaws.com", {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Action": [
                "bedrock:InvokeModel", "bedrock:Retrieve", "bedrock-agent-runtime:Retrieve",
                "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Scan",
                "s3:GetObject"],
                "Resource": "*"}],
        })
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] IAM role 建立失敗:{e}")
        print("→ 請在 Console 手動建立上述兩個 role(說明見 docstring),或改用既有 role,"
              "然後把 arn 填入本腳本 ensure_role 回傳處重跑。")
        sys.exit(1)

    existing = [s for s in apprunner.list_services()["ServiceSummaryList"] if s["ServiceName"] == SERVICE]
    if existing:
        print(f"[SKIP] App Runner 服務已存在:{existing[0]['ServiceUrl']}")
        return

    svc = apprunner.create_service(
        ServiceName=SERVICE,
        SourceConfiguration={
            "AuthenticationConfiguration": {"AccessRoleArn": access_arn},
            "AutoDeploymentsEnabled": False,
            "ImageRepository": {
                "ImageIdentifier": IMAGE,
                "ImageRepositoryType": "ECR",
                "ImageConfiguration": {
                    "Port": "8000",
                    "RuntimeEnvironmentVariables": load_env(),
                },
            },
        },
        InstanceConfiguration={"Cpu": "1024", "Memory": "2048", "InstanceRoleArn": instance_arn},
        HealthCheckConfiguration={"Protocol": "HTTP", "Path": "/api/health"},
    )["Service"]
    print(f"[CREATING] {svc['ServiceArn']}")
    while True:
        time.sleep(20)
        cur = apprunner.describe_service(ServiceArn=svc["ServiceArn"])["Service"]
        print(f"  status: {cur['Status']}")
        if cur["Status"] != "OPERATION_IN_PROGRESS":
            break
    print(f"[DONE] https://{cur['ServiceUrl']}")


if __name__ == "__main__":
    main()
