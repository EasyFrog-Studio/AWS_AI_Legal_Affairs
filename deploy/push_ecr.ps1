# 建 ECR repo(冪等)→ docker build → push
# 用法:cd AWS_AI_Legal_Affairs 後執行 .\deploy\push_ecr.ps1
$ErrorActionPreference = "Stop"
$REGION = "us-west-2"
# 帳號 ID 留在 .env(不進版控);環境變數優先,方便 CI 不落檔
$ACCOUNT = $env:AWS_ACCOUNT_ID
if (-not $ACCOUNT) {
    $envFile = Join-Path $PSScriptRoot "..\.env"
    if (Test-Path $envFile) {
        $hit = Select-String -Path $envFile -Pattern '^\s*AWS_ACCOUNT_ID\s*=\s*(\S+)'
        if ($hit) { $ACCOUNT = $hit.Matches[0].Groups[1].Value }
    }
}
if (-not $ACCOUNT) { throw "AWS_ACCOUNT_ID 未設定:填在 AWS_AI_Legal_Affairs/.env(範本見 .env.example)" }
$REPO = "appeal-ai"
$REGISTRY = "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

aws ecr describe-repositories --repository-names $REPO --region $REGION 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[CREATE] ECR repo $REPO"
    aws ecr create-repository --repository-name $REPO --region $REGION | Out-Null
} else {
    Write-Host "[SKIP] ECR repo $REPO 已存在"
}

aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REGISTRY
if ($LASTEXITCODE -ne 0) { throw "ECR 登入失敗(檢查憑證是否過期)" }

docker build -f docker/Dockerfile -t "${REPO}:latest" .
if ($LASTEXITCODE -ne 0) { throw "docker build 失敗" }

docker tag "${REPO}:latest" "${REGISTRY}/${REPO}:latest"
docker push "${REGISTRY}/${REPO}:latest"
if ($LASTEXITCODE -ne 0) { throw "docker push 失敗" }

Write-Host "[DONE] image: ${REGISTRY}/${REPO}:latest"
