"""ECS Fargate 部署(App Runner 被 SCP 封鎖的替代):跑現有 ECR image,task 直接以 public IP:8000 對外,資源已存在就沿用。"""
import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-west-2"
ACCOUNT = "047877300727"
IMAGE = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/appeal-ai:latest"
CLUSTER = "appeal-ai"
FAMILY = "appeal-ai"
SERVICE = "appeal-ai-svc"
LOG_GROUP = "/ecs/appeal-ai"
EXEC_ROLE = "appeal-ecs-exec"
TASK_ROLE = "appeal-ecs-task"
SG_NAME = "appeal-ai-sg"
PORT = 8000

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _REPO_ROOT / ".env"

ENV = {
    "AI_PROVIDER": "aws",
    "AWS_REGION": REGION,
    "BEDROCK_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
    "KB_LAW_ID": "Y3REHA6HNN",
    "KB_CASE_ID": "BUMDFYCWCM",
    "S3_BUCKET": f"appeal-ai-{ACCOUNT}",
    "DDB_LAW_TABLE": "appeal_law_articles",
    "DDB_CASE_TABLE": "appeal_cases",
}

iam = boto3.client("iam")
ec2 = boto3.client("ec2", region_name=REGION)
ecs = boto3.client("ecs", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)


def _parse_env_file(path: Path) -> dict:
    """極簡 KEY=VALUE 逐行解析:忽略 # 開頭整行與行內「  #」之後的註解。"""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = line.split("  #", 1)[0].partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return values


def resolve_api_key(environ, env_file) -> str:
    """API_KEY 一律不硬編:environ 優先,其次 .env 檔,都無則中止部署。"""
    api_key = environ.get("API_KEY")
    if api_key:
        return api_key
    if env_file is not None:
        api_key = _parse_env_file(Path(env_file)).get("API_KEY")
        if api_key:
            return api_key
    raise SystemExit("API_KEY 未設定:請在 .env 或環境變數提供")


def ensure_role(name, policy_arns=None, inline=None):
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
             "Principal": {"Service": "ecs-tasks.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        time.sleep(8)
    for p in policy_arns or []:
        iam.attach_role_policy(RoleName=name, PolicyArn=p)
    if inline:
        iam.put_role_policy(RoleName=name, PolicyName=f"{name}-inline", PolicyDocument=json.dumps(inline))
    return arn


def ensure_log_group():
    try:
        logs.create_log_group(logGroupName=LOG_GROUP)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass


def default_vpc_and_subnets():
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        vpcs = ec2.describe_vpcs()["Vpcs"]
    vpc_id = vpcs[0]["VpcId"]
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]},
                                            {"Name": "map-public-ip-on-launch", "Values": ["true"]}])["Subnets"]
    if not subnets:
        subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
    return vpc_id, [s["SubnetId"] for s in subnets[:2]]


def ensure_sg(vpc_id):
    existing = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [SG_NAME]},
                                                     {"Name": "vpc-id", "Values": [vpc_id]}])["SecurityGroups"]
    if existing:
        return existing[0]["GroupId"]
    sg_id = ec2.create_security_group(GroupName=SG_NAME, Description="appeal-ai fargate",
                                      VpcId=vpc_id)["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{
        "IpProtocol": "tcp", "FromPort": PORT, "ToPort": PORT,
        "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "app port"}]}])
    return sg_id


def register_task_def(exec_arn, task_arn):
    return ecs.register_task_definition(
        family=FAMILY, networkMode="awsvpc", requiresCompatibilities=["FARGATE"],
        cpu="1024", memory="2048", executionRoleArn=exec_arn, taskRoleArn=task_arn,
        containerDefinitions=[{
            "name": "web", "image": IMAGE, "essential": True,
            "portMappings": [{"containerPort": PORT, "protocol": "tcp"}],
            "environment": [{"name": k, "value": v} for k, v in ENV.items()],
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": LOG_GROUP, "awslogs-region": REGION,
                "awslogs-stream-prefix": "web"}},
        }])["taskDefinition"]["taskDefinitionArn"]


def get_public_ip(task_arn):
    for _ in range(60):
        time.sleep(10)
        t = ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])["tasks"][0]
        last = t.get("lastStatus")
        print(f"  task status: {last}")
        if last == "RUNNING":
            eni = next(d["value"] for a in t["attachments"] for d in a["details"]
                       if d["name"] == "networkInterfaceId")
            ni = ec2.describe_network_interfaces(NetworkInterfaceIds=[eni])["NetworkInterfaces"][0]
            return ni.get("Association", {}).get("PublicIp")
        if last == "STOPPED":
            reason = t.get("stoppedReason", "")
            cont = "; ".join(f"{c.get('name')}:{c.get('reason','')}" for c in t.get("containers", []))
            print(f"[FAIL] task STOPPED: {reason} | {cont}")
            sys.exit(1)
    return None


def main():
    ENV["API_KEY"] = resolve_api_key(os.environ, _ENV_FILE)
    try:
        exec_arn = ensure_role(EXEC_ROLE,
            policy_arns=["arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"])
        task_arn = ensure_role(TASK_ROLE, inline={"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:Retrieve",
                "bedrock-agent-runtime:Retrieve", "dynamodb:GetItem", "dynamodb:PutItem",
                "dynamodb:UpdateItem", "dynamodb:Scan", "dynamodb:BatchGetItem", "s3:GetObject"],
            "Resource": "*"}]})
    except ClientError as e:
        print(f"[FAIL] IAM role 建立失敗:{e}")
        sys.exit(1)

    ensure_log_group()
    vpc_id, subnets = default_vpc_and_subnets()
    sg_id = ensure_sg(vpc_id)
    print(f"vpc={vpc_id} subnets={subnets} sg={sg_id}")

    try:
        ecs.create_cluster(clusterName=CLUSTER)
    except ClientError:
        pass

    td_arn = register_task_def(exec_arn, task_arn)
    print(f"task definition: {td_arn}")

    net = {"awsvpcConfiguration": {"subnets": subnets, "securityGroups": [sg_id],
                                   "assignPublicIp": "ENABLED"}}
    svcs = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"]
    if svcs and svcs[0]["status"] == "ACTIVE":
        ecs.update_service(cluster=CLUSTER, service=SERVICE, taskDefinition=td_arn,
                           desiredCount=1, forceNewDeployment=True)
        print("[UPDATE] 服務已更新,重新部署中")
    else:
        ecs.create_service(cluster=CLUSTER, serviceName=SERVICE, taskDefinition=td_arn,
                           desiredCount=1, launchType="FARGATE", networkConfiguration=net)
        print("[CREATE] 服務建立中")

    for _ in range(60):
        time.sleep(10)
        tasks = ecs.list_tasks(cluster=CLUSTER, serviceName=SERVICE, desiredStatus="RUNNING")["taskArns"]
        if tasks:
            ip = get_public_ip(tasks[0])
            if ip:
                print(f"\n[DONE] 公開網址: http://{ip}:{PORT}")
                print("       API Key: 見 .env")
                return
    print("[WARN] task 尚未進入 RUNNING,請稍後用 AWS console 查 ECS 服務 appeal-ai-svc")


if __name__ == "__main__":
    main()
