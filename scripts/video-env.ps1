param(
    [ValidateSet('Prepare', 'Status', 'Test', 'Restart')]
    [string]$Action = 'Status',
    [ValidateSet('recording', 'rehearsal')]
    [string]$Environment = 'recording'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$artifactDirectory = Join-Path $projectRoot 'tmp/video'
$null = New-Item -ItemType Directory -Path $artifactDirectory -Force
$projectName = "checkstock-video-$Environment"
$composePath = Join-Path $artifactDirectory "$Environment.compose.yaml"
$appPort = if ($Environment -eq 'recording') { 8002 } else { 8003 }
$dbPort = if ($Environment -eq 'recording') { 5434 } else { 5435 }
$baseUrl = "http://127.0.0.1:$appPort"

function Invoke-VideoDocker {
    param([string[]]$DockerArguments)
    & docker compose -p $projectName -f $composePath @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw "Docker exited with code $LASTEXITCODE" }
}

function Get-VideoSnapshot {
    Invoke-RestMethod "$baseUrl/api/v1/state" -TimeoutSec 15 |
        ConvertTo-Json -Depth 50 -Compress
}

function Wait-VideoHealth {
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        try {
            $health = Invoke-RestMethod "$baseUrl/health" -TimeoutSec 2
            if ($health.status -eq 'ok') { return }
        } catch { }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Application did not become healthy: $baseUrl"
}

if ($Action -eq 'Prepare') {
    $buildContext = $projectRoot.Replace('\', '/').Replace("'", "''")
    # A complete standalone file avoids merging the main project's published ports.
    $compose = @"
name: $projectName
services:
  db:
    image: postgres:18-alpine
    environment:
      POSTGRES_USER: checkstock
      POSTGRES_PASSWORD: checkstock-local-only
      POSTGRES_DB: checkstock
    ports:
      - '127.0.0.1:${dbPort}:5432'
    volumes:
      - postgres_data:/var/lib/postgresql
    healthcheck:
      test: ['CMD-SHELL', 'pg_isready -U checkstock -d checkstock']
      interval: 2s
      timeout: 3s
      retries: 30
  app:
    image: checkstock-video-app:local
    build:
      context: '$buildContext'
    environment:
      DATABASE_URL: postgresql+psycopg://checkstock:checkstock-local-only@db:5432/checkstock
      DEMO_SEED: 'true'
    ports:
      - '127.0.0.1:${appPort}:8000'
    depends_on:
      db:
        condition: service_healthy
    healthcheck:
      test: ['CMD', 'python', '-c', "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"]
      interval: 2s
      timeout: 3s
      retries: 30
volumes:
  postgres_data:
"@
    [IO.File]::WriteAllText($composePath, $compose, [Text.UTF8Encoding]::new($false))
    Invoke-VideoDocker -DockerArguments @('up', '--build', '-d', '--wait', '--wait-timeout', '120')
} else {
    if (-not (Test-Path -LiteralPath $composePath)) {
        throw "Run -Action Prepare -Environment $Environment first."
    }
    switch ($Action) {
        'Status' {
            Invoke-VideoDocker -DockerArguments @('ps')
            $snapshot = Get-VideoSnapshot | ConvertFrom-Json
            $snapshot.summary | Format-List
            $snapshot.integrity | Format-List
            Write-Output "Documents: $($snapshot.documents.Count); events: $($snapshot.history.Count)"
        }
        'Test' {
            $before = Get-VideoSnapshot
            $startedAt = [DateTimeOffset]::UtcNow.ToString('o')
            $testLines = & docker compose -p $projectName -f $composePath run --rm app python -m pytest -q 2>&1
            $testExit = $LASTEXITCODE
            $testOutput = ($testLines | ForEach-Object { "$_" }) -join [Environment]::NewLine
            $log = "UTC: $startedAt`nProject: $projectName`nCommand: docker compose -p $projectName -f $composePath run --rm app python -m pytest -q`n`n$testOutput`nExit code: $testExit`n"
            $logPath = Join-Path $artifactDirectory "$Environment-tests.txt"
            [IO.File]::WriteAllText($logPath, $log, [Text.UTF8Encoding]::new($false))
            Write-Output $testOutput
            $after = Get-VideoSnapshot
            if ($before -cne $after) { throw 'Public demonstration state changed during tests.' }
            if ($testExit -ne 0) { throw "Tests failed; see $logPath" }
            Write-Output "PASS: public state unchanged. Actual test output: $logPath"
        }
        'Restart' {
            $before = Get-VideoSnapshot
            Invoke-VideoDocker -DockerArguments @('restart', 'app')
            Wait-VideoHealth
            $after = Get-VideoSnapshot
            $unchanged = $before -ceq $after
            $report = [ordered]@{
                checked_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
                project = $projectName
                health = 'ok'
                state_unchanged = $unchanged
                before = $before | ConvertFrom-Json
                after = $after | ConvertFrom-Json
            } | ConvertTo-Json -Depth 50
            $reportPath = Join-Path $artifactDirectory "$Environment-restart.json"
            [IO.File]::WriteAllText($reportPath, $report, [Text.UTF8Encoding]::new($false))
            if (-not $unchanged) { throw "State changed after restart; see $reportPath" }
            Write-Output "PASS: /health is ok; complete API state unchanged. Report: $reportPath"
        }
    }
}

Write-Output "Application: $baseUrl"
Write-Output "DBeaver: 127.0.0.1:$dbPort / database: checkstock / user: checkstock"
Write-Output "Compose: $composePath"
Write-Output 'Prepare preserves existing data; it does not reset a previous rehearsal or recording.'
