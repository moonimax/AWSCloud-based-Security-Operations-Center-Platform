param(
    [string]$AwsProfile = "default",
    [string]$Region = "ap-northeast-2",
    [string]$ModelId = "apac.amazon.nova-micro-v1:0",
    [switch]$PrepareOnly
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
$envNames = @("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REGION", "BEDROCK_MODEL_ID")
$previous = @{}
foreach ($name in $envNames) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }
Push-Location $projectRoot
try {
    # Read-only AWS calls: obtain actual profile and destination model ARNs.
    $profileJson = aws bedrock get-inference-profile --inference-profile-identifier $ModelId --region $Region --profile $AwsProfile --output json --no-cli-pager
    if ($LASTEXITCODE -ne 0) { throw "Profile lookup failed. Check region, model ID and bedrock:GetInferenceProfile permission." }
    $profileData = $profileJson | ConvertFrom-Json
    if ($profileData.status -ne "ACTIVE") { throw "Inference profile is not ACTIVE." }
    $modelArns = @($profileData.models | ForEach-Object { $_.modelArn })
    if (-not $profileData.inferenceProfileArn -or $modelArns.Count -eq 0) { throw "Incomplete inference profile response." }
    $resources = @($profileData.inferenceProfileArn) + $modelArns
    $policy = @{
        Version = "2012-10-17"
        Statement = @(
            @{ Effect = "Allow"; Action = @("bedrock:GetInferenceProfile"); Resource = @($profileData.inferenceProfileArn) },
            @{ Effect = "Allow"; Action = @("bedrock:InvokeModel"); Resource = $resources }
        )
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $projectRoot "runtime/nova-iam-policy.json"), ($policy | ConvertTo-Json -Depth 10), $utf8)
    $tfvars = @{ region = $Region; bedrock_model_id = $ModelId; bedrock_resource_arns = $resources }
    [IO.File]::WriteAllText((Join-Path $projectRoot "runtime/nova-bedrock.tfvars.json"), ($tfvars | ConvertTo-Json -Depth 10), $utf8)
    Write-Host "Prepared runtime/nova-iam-policy.json and runtime/nova-bedrock.tfvars.json (no credentials)."
    if ($PrepareOnly) { return }
    python scripts/init_local.py
    if ($LASTEXITCODE -ne 0) { throw "Secret initialization failed; containers were not started." }
    $credentialJson = aws configure export-credentials --profile $AwsProfile --format process
    if ($LASTEXITCODE -ne 0) { throw "Credential export failed. Check profile or run aws sso login." }
    $credentialData = $credentialJson | ConvertFrom-Json
    if (-not $credentialData.AccessKeyId -or -not $credentialData.SecretAccessKey) { throw "AWS credentials are missing." }
    $env:AWS_ACCESS_KEY_ID = $credentialData.AccessKeyId
    $env:AWS_SECRET_ACCESS_KEY = $credentialData.SecretAccessKey
    $env:AWS_SESSION_TOKEN = $credentialData.SessionToken
    $env:AWS_REGION = $Region
    $env:BEDROCK_MODEL_ID = $ModelId
    Set-Location runtime
    docker compose -f compose.yaml -f compose.nova.local.yaml --profile demo up -d --build --force-recreate
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed." }
    Write-Host "Open http://localhost:8080. Login: runtime/secrets/credentials.txt"
    Write-Host "One billed check: docker compose exec agent python check_bedrock.py"
} finally {
    foreach ($name in $envNames) { [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process") }
    Pop-Location
}
