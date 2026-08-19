"""在 ECS Fargate 服務前架 ALB,取得固定 DNS 網址(冪等,可重複執行)。

執行:python deploy/alb.py(任意工作目錄皆可)
產出:http://<ALB DNS>(不隨 task 重啟改變)
前置:ecs_fargate.py 已建立 cluster appeal-ai / service appeal-ai-svc / SG appeal-ai-sg。
HTTPS 需自有網域 + ACM 憑證,拿到網域後在 listener 上加 443 即可升級。
"""
import time

import boto3

REGION = "us-west-2"
ALB_NAME = "appeal-ai-alb"
TG_NAME = "appeal-ai-tg"
SG_NAME = "appeal-ai-sg"
CLUSTER = "appeal-ai"
SERVICE = "appeal-ai-svc"
APP_PORT = 8000

ec2 = boto3.client("ec2", region_name=REGION)
elb = boto3.client("elbv2", region_name=REGION)
ecs = boto3.client("ecs", region_name=REGION)


def main() -> None:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    vpc_id = vpcs[0]["VpcId"] if vpcs else ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
    by_az = {}
    for s in ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]:
        by_az.setdefault(s["AvailabilityZone"], s["SubnetId"])
    subnet_ids = list(by_az.values())[:3]

    sg_id = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SG_NAME]},
                 {"Name": "vpc-id", "Values": [vpc_id]}])["SecurityGroups"][0]["GroupId"]
    try:
        ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
            "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
        print("[CREATE] SG 開 80")
    except ec2.exceptions.ClientError:
        print("[SKIP] SG 80 已開")

    lbs = [l for l in elb.describe_load_balancers()["LoadBalancers"] if l["LoadBalancerName"] == ALB_NAME]
    if lbs:
        lb = lbs[0]
        print("[SKIP] ALB 已存在")
    else:
        lb = elb.create_load_balancer(Name=ALB_NAME, Subnets=subnet_ids, SecurityGroups=[sg_id],
                                      Scheme="internet-facing", Type="application")["LoadBalancers"][0]
        print("[CREATE] ALB:", lb["LoadBalancerArn"])

    tgs = [t for t in elb.describe_target_groups()["TargetGroups"] if t["TargetGroupName"] == TG_NAME]
    if tgs:
        tg_arn = tgs[0]["TargetGroupArn"]
        print("[SKIP] target group 已存在")
    else:
        tg_arn = elb.create_target_group(
            Name=TG_NAME, Protocol="HTTP", Port=APP_PORT, VpcId=vpc_id, TargetType="ip",
            HealthCheckPath="/api/health", HealthCheckIntervalSeconds=15,
            HealthyThresholdCount=2)["TargetGroups"][0]["TargetGroupArn"]
        print("[CREATE] target group:", tg_arn)

    if not elb.describe_listeners(LoadBalancerArn=lb["LoadBalancerArn"])["Listeners"]:
        elb.create_listener(LoadBalancerArn=lb["LoadBalancerArn"], Protocol="HTTP", Port=80,
                            DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}])
        print("[CREATE] listener 80")
    else:
        print("[SKIP] listener 已存在")

    svc = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]
    attached = any(lbc.get("targetGroupArn") == tg_arn for lbc in svc.get("loadBalancers", []))
    if attached:
        print("[SKIP] 服務已掛 target group")
    else:
        ecs.update_service(cluster=CLUSTER, service=SERVICE,
                           loadBalancers=[{"targetGroupArn": tg_arn,
                                           "containerName": "web", "containerPort": APP_PORT}],
                           forceNewDeployment=True)
        print("[UPDATE] 服務掛上 target group,rolling 部署中")

    for i in range(40):
        time.sleep(15)
        th = elb.describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]
        states = [h["TargetHealth"]["State"] for h in th]
        print(f"  target health: {states or ['(registering)']}")
        if "healthy" in states:
            break

    lb = elb.describe_load_balancers(Names=[ALB_NAME])["LoadBalancers"][0]
    print(f"[DONE] 固定網址: http://{lb['DNSName']}")


if __name__ == "__main__":
    main()
