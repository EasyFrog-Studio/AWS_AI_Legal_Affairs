"""ECS Fargate + ALB 部署:ALB 只放行白名單 IP,task 只收 ALB,兩者都不對 0.0.0.0/0 開。

黑客松規範禁止對外完全開放的 Security Group,故 SG 規則採「對帳」而非「存在就跳過」:
每次執行都會撤掉白名單以外的 ingress,手動在 console 加開的洞不會留到下一次部署。
"""
import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH, override=False)


def require_env(name: str) -> str:
    """帳號 ID 與白名單留在 .env(不進版控);缺了就中止,不用預設值頂替。"""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} 未設定:填在 {ENV_PATH}(範本見 .env.example)")
    return value


def parse_allowed_ingress(raw: str) -> list[tuple[str, str]]:
    """把 `CIDR=說明` 的逗號清單解析成 SG 規則;解析不出來一律拋,不靜靜放行或靜靜清空。"""
    entries: list[tuple[str, str]] = []
    for item in (part.strip() for part in (raw or "").split(",")):
        if not item:
            continue
        cidr, sep, note = item.partition("=")
        cidr, note = cidr.strip(), note.strip()
        if not sep or not note:
            raise ValueError(f"白名單項目要寫成 CIDR=說明:{item!r}")
        if not cidr.endswith("/32"):
            raise ValueError(f"白名單只收單一主機 /32:{cidr!r}")
        entries.append((cidr, note))
    if not entries:
        raise ValueError(f"DEPLOY_ALLOWED_INGRESS 未設定或全空:填在 {ENV_PATH}")
    return entries


REGION = "us-west-2"
ACCOUNT = require_env("AWS_ACCOUNT_ID")
REPO = "appeal-ai"
IMAGE = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{REPO}:latest"
CLUSTER = "appeal-ai"
FAMILY = "appeal-ai"
SERVICE = "appeal-ai-svc"
LOG_GROUP = "/ecs/appeal-ai"
EXEC_ROLE = "appeal-ecs-exec"
TASK_ROLE = "appeal-ecs-task"
ALB_SG_NAME = "appeal-ai-alb-sg"
TASK_SG_NAME = "appeal-ai-task-sg"
ALB_NAME = "appeal-ai-alb"
TG_NAME = "appeal-ai-tg"
PORT = 8000
ALB_PORT = 80

S3_BUCKET = f"appeal-ai-{ACCOUNT}"
KB_LAW_ID = "WQVGZBCEUA"
KB_CASE_ID = "HEVPST3YK1"
KB_INTERPRETATION_ID = "ROM2C4XC2J"
KB_RULING_ID = "I1PEFUMBIZ"
KB_JUDGMENT_ID = "YHTYVE9RJG"
DDB_LAW_TABLE = "appeal_law_articles"
DDB_CASE_TABLE = "appeal_cases"
DDB_PAST_DECISIONS_TABLE = "appeal_past_decisions"
DDB_INTERPRETATION_TABLE = "appeal_interpretations"
DDB_RULING_TABLE = "appeal_rulings"
DDB_JUDGMENT_TABLE = "appeal_judgments"

# 這份清單就是對外暴露面的全部,故留在 .env 而不進版控
ALLOWED_INGRESS = parse_allowed_ingress(os.environ.get("DEPLOY_ALLOWED_INGRESS", ""))

# 登入頁已公告這把金鑰;環境變數 API_KEY 可在部署時覆蓋
CLOUD_API_KEY = "0000"

ENV = {
    "AI_PROVIDER": "aws",
    "AWS_REGION": REGION,
    "BEDROCK_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
    "BEDROCK_MIN_INTERVAL_SECONDS": "1.0",
    "KB_LAW_ID": KB_LAW_ID,
    "KB_CASE_ID": KB_CASE_ID,
    "KB_INTERPRETATION_ID": KB_INTERPRETATION_ID,
    "KB_RULING_ID": KB_RULING_ID,
    "KB_JUDGMENT_ID": KB_JUDGMENT_ID,
    "S3_BUCKET": S3_BUCKET,
    "DDB_LAW_TABLE": DDB_LAW_TABLE,
    # 這張表的主鍵是 law_id,「法規名稱#條號」只是 GSI,故查詢走索引而非 batch_get
    "DDB_LAW_INDEX": "law-article-index",
    "DDB_LAW_DATE_FIELD": "revised_date",
    "DDB_CASE_TABLE": DDB_CASE_TABLE,
    "DDB_PAST_DECISIONS_TABLE": DDB_PAST_DECISIONS_TABLE,
    "DDB_INTERPRETATION_TABLE": DDB_INTERPRETATION_TABLE,
    "DDB_RULING_TABLE": DDB_RULING_TABLE,
    "DDB_JUDGMENT_TABLE": DDB_JUDGMENT_TABLE,
}

iam = boto3.client("iam")
ec2 = boto3.client("ec2", region_name=REGION)
ecs = boto3.client("ecs", region_name=REGION)
elb = boto3.client("elbv2", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)


def resolve_api_key(environ) -> str:
    """雲端金鑰不從 .env 讀:那把是開發者本機在用的,混用會讓示範結束後還得改本地設定。"""
    return environ.get("API_KEY") or CLOUD_API_KEY


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


def task_policy():
    """Resource 逐項收斂;foundation-model 的區域留萬用字元,跨區推論設定檔會把請求送到別區的同一個模型。"""
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "BedrockInvoke", "Effect": "Allow",
         "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": ["arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6*",
                      f"arn:aws:bedrock:{REGION}:{ACCOUNT}:inference-profile/*"]},
        {"Sid": "BedrockRetrieve", "Effect": "Allow", "Action": ["bedrock:Retrieve"],
         "Resource": [f"arn:aws:bedrock:{REGION}:{ACCOUNT}:knowledge-base/{KB_LAW_ID}",
                      f"arn:aws:bedrock:{REGION}:{ACCOUNT}:knowledge-base/{KB_CASE_ID}",
                      f"arn:aws:bedrock:{REGION}:{ACCOUNT}:knowledge-base/{KB_INTERPRETATION_ID}",
                      f"arn:aws:bedrock:{REGION}:{ACCOUNT}:knowledge-base/{KB_RULING_ID}",
                      f"arn:aws:bedrock:{REGION}:{ACCOUNT}:knowledge-base/{KB_JUDGMENT_ID}"]},
        {"Sid": "CaseObjects", "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
         "Resource": f"arn:aws:s3:::{S3_BUCKET}/*"},
        # 沒有 ListBucket 時 GetObject 對不存在的 key 回 AccessDenied,後端分不出 404
        {"Sid": "BucketList", "Effect": "Allow", "Action": ["s3:ListBucket"],
         "Resource": f"arn:aws:s3:::{S3_BUCKET}"},
        {"Sid": "CaseTable", "Effect": "Allow",
         "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Scan"],
         "Resource": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{DDB_CASE_TABLE}"},
        {"Sid": "LawTable", "Effect": "Allow",
         "Action": ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query"],
         "Resource": [f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{DDB_LAW_TABLE}",
                      f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{DDB_LAW_TABLE}/index/*"]},
        # 過往決定書總覽 + 參考見解三類:PK 精查用,四張表都沒有 GSI
        {"Sid": "ReferenceTables", "Effect": "Allow",
         "Action": ["dynamodb:GetItem", "dynamodb:BatchGetItem"],
         "Resource": [f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{DDB_PAST_DECISIONS_TABLE}",
                      f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{DDB_INTERPRETATION_TABLE}",
                      f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{DDB_RULING_TABLE}",
                      f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{DDB_JUDGMENT_TABLE}"]},
    ]}


def ensure_log_group():
    try:
        logs.create_log_group(logGroupName=LOG_GROUP)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass


def default_vpc_and_subnets():
    """ALB 至少要兩個 AZ;task 需要 public subnet,帳號內沒有 NAT Gateway,私有子網拉不到 ECR image。"""
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        vpcs = ec2.describe_vpcs()["Vpcs"]
    vpc_id = vpcs[0]["VpcId"]
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]},
                                            {"Name": "map-public-ip-on-launch", "Values": ["true"]}])["Subnets"]
    by_az = {}
    for s in subnets:
        by_az.setdefault(s["AvailabilityZone"], s["SubnetId"])
    if len(by_az) < 2:
        raise SystemExit(f"public subnet 只覆蓋 {len(by_az)} 個 AZ，ALB 至少需要 2 個")
    return vpc_id, list(by_az.values())


def find_or_create_sg(vpc_id, name, description):
    existing = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [name]},
                                                     {"Name": "vpc-id", "Values": [vpc_id]}])["SecurityGroups"]
    if existing:
        return existing[0]["GroupId"]
    return ec2.create_security_group(GroupName=name, Description=description, VpcId=vpc_id)["GroupId"]


def reconcile_ingress(sg_id, wanted):
    """把 SG 的 ingress 對到 wanted 這份清單:多的撤掉、少的補上。

    存在就跳過會讓先前開的 0.0.0.0/0 永遠留著,而規範明令禁止對外完全開放,所以每次都要對帳。
    """
    current = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]["IpPermissions"]
    if current:
        ec2.revoke_security_group_ingress(GroupId=sg_id, IpPermissions=current)
        print(f"  [REVOKE] {sg_id} 撤掉 {len(current)} 條既有 ingress")
    if wanted:
        ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=wanted)
        print(f"  [ALLOW] {sg_id} 套上 {len(wanted)} 條 ingress")


def ensure_security_groups(vpc_id):
    alb_sg = find_or_create_sg(vpc_id, ALB_SG_NAME, "appeal-ai ALB: judge IP allowlist only")
    task_sg = find_or_create_sg(vpc_id, TASK_SG_NAME, "appeal-ai task: from ALB only")
    reconcile_ingress(alb_sg, [{
        "IpProtocol": "tcp", "FromPort": ALB_PORT, "ToPort": ALB_PORT,
        "IpRanges": [{"CidrIp": cidr, "Description": note} for cidr, note in ALLOWED_INGRESS]}])
    reconcile_ingress(task_sg, [{
        "IpProtocol": "tcp", "FromPort": PORT, "ToPort": PORT,
        "UserIdGroupPairs": [{"GroupId": alb_sg, "Description": "alb only"}]}])
    return alb_sg, task_sg


def ensure_alb(vpc_id, subnets, alb_sg):
    try:
        lb = elb.describe_load_balancers(Names=[ALB_NAME])["LoadBalancers"][0]
        print(f"[SKIP] ALB 已存在: {lb['DNSName']}")
    except elb.exceptions.LoadBalancerNotFoundException:
        lb = elb.create_load_balancer(Name=ALB_NAME, Subnets=subnets, SecurityGroups=[alb_sg],
                                      Scheme="internet-facing", Type="application",
                                      IpAddressType="ipv4")["LoadBalancers"][0]
        print(f"[CREATE] ALB: {lb['LoadBalancerArn']}")
        elb.get_waiter("load_balancer_available").wait(LoadBalancerArns=[lb["LoadBalancerArn"]])
    elb.set_security_groups(LoadBalancerArn=lb["LoadBalancerArn"], SecurityGroups=[alb_sg])

    try:
        tg_arn = elb.describe_target_groups(Names=[TG_NAME])["TargetGroups"][0]["TargetGroupArn"]
        print("[SKIP] target group 已存在")
    except elb.exceptions.TargetGroupNotFoundException:
        tg_arn = elb.create_target_group(
            Name=TG_NAME, Protocol="HTTP", Port=PORT, VpcId=vpc_id, TargetType="ip",
            HealthCheckPath="/api/health", HealthCheckIntervalSeconds=30,
            HealthCheckTimeoutSeconds=10, HealthyThresholdCount=2,
            UnhealthyThresholdCount=5)["TargetGroups"][0]["TargetGroupArn"]
        print(f"[CREATE] target group: {tg_arn}")

    listeners = elb.describe_listeners(LoadBalancerArn=lb["LoadBalancerArn"])["Listeners"]
    if listeners:
        print("[SKIP] listener 已存在")
    else:
        elb.create_listener(LoadBalancerArn=lb["LoadBalancerArn"], Protocol="HTTP", Port=ALB_PORT,
                            DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}])
        print(f"[CREATE] listener {ALB_PORT}")
    return lb["DNSName"], tg_arn


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


def ensure_service(td_arn, subnets, task_sg, tg_arn):
    net = {"awsvpcConfiguration": {"subnets": subnets, "securityGroups": [task_sg],
                                   "assignPublicIp": "ENABLED"}}
    lb_cfg = [{"targetGroupArn": tg_arn, "containerName": "web", "containerPort": PORT}]
    svcs = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"]
    if svcs and svcs[0]["status"] == "ACTIVE":
        ecs.update_service(cluster=CLUSTER, service=SERVICE, taskDefinition=td_arn,
                           desiredCount=1, networkConfiguration=net, loadBalancers=lb_cfg,
                           healthCheckGracePeriodSeconds=180, forceNewDeployment=True)
        print("[UPDATE] 服務已更新,rolling 部署中")
    else:
        ecs.create_service(cluster=CLUSTER, serviceName=SERVICE, taskDefinition=td_arn,
                           desiredCount=1, launchType="FARGATE", networkConfiguration=net,
                           loadBalancers=lb_cfg, healthCheckGracePeriodSeconds=180)
        print("[CREATE] 服務建立中")


def wait_healthy(tg_arn, dns_name):
    """等服務的 primary deployment 收斂,不是等「有任何 target healthy」。

    滾動更新期間舊 task 仍然健康,只看 target health 會在新版還在啟動時就報成功。
    """
    for _ in range(60):
        time.sleep(15)
        primary = next(d for d in ecs.describe_services(cluster=CLUSTER, services=[SERVICE])
                       ["services"][0]["deployments"] if d["status"] == "PRIMARY")
        th = elb.describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]
        states = [h["TargetHealth"]["State"] for h in th]
        rollout = primary.get("rolloutState")
        print(f"  rollout={rollout} running={primary['runningCount']}/{primary['desiredCount']} "
              f"targets={states or ['(registering)']}")
        if rollout == "COMPLETED" and "healthy" in states:
            print(f"\n[DONE] 公開網址: http://{dns_name}")
            return True
        if rollout == "FAILED":
            print(f"[FAIL] 部署失敗:{primary.get('rolloutStateReason')}")
            return False
        for h in th:
            if h["TargetHealth"]["State"] == "unhealthy":
                print(f"  [WARN] unhealthy: {h['TargetHealth'].get('Reason')} "
                      f"{h['TargetHealth'].get('Description')}")
    print(f"[WARN] 部署未在時限內收斂,請查 CloudWatch log group {LOG_GROUP}")
    return False


def main():
    ENV["API_KEY"] = resolve_api_key(os.environ)
    source = "環境變數 API_KEY" if os.environ.get("API_KEY") else f"CLOUD_API_KEY({CLOUD_API_KEY})"
    print(f"API_KEY 來源:{source}")
    try:
        exec_arn = ensure_role(EXEC_ROLE,
            policy_arns=["arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"])
        task_arn = ensure_role(TASK_ROLE, inline=task_policy())
    except ClientError as e:
        print(f"[FAIL] IAM role 建立失敗:{e}")
        sys.exit(1)

    ensure_log_group()
    vpc_id, subnets = default_vpc_and_subnets()
    alb_sg, task_sg = ensure_security_groups(vpc_id)
    print(f"vpc={vpc_id} subnets={subnets} alb_sg={alb_sg} task_sg={task_sg}")

    try:
        ecs.create_cluster(clusterName=CLUSTER)
    except ClientError:
        pass

    dns_name, tg_arn = ensure_alb(vpc_id, subnets, alb_sg)
    td_arn = register_task_def(exec_arn, task_arn)
    print(f"task definition: {td_arn}")
    ensure_service(td_arn, subnets, task_sg, tg_arn)
    if not wait_healthy(tg_arn, dns_name):
        sys.exit(1)


if __name__ == "__main__":
    main()
