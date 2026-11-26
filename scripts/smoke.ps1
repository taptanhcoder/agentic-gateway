# Requires PowerShell 7. Python, Alembic and SQL run only inside containers.
[CmdletBinding()]
param(
    [string]$Image = 'agentic-gateway:dev',
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repo = Split-Path $PSScriptRoot -Parent
$project = 'ag-smoke-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$envFile = Join-Path $repo ".env.$project"
$results = [Collections.Generic.List[object]]::new()
$failure = $null
$cleaned = $false
$previousEnvironment = @{}

function Run-Docker([string[]]$Command) {
    $output = & docker @Command 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed: $($output -join [Environment]::NewLine)"
    }
    $output | ForEach-Object { $_.ToString() }
}

function Compose([string[]]$Command) {
    Run-Docker -Command ($script:composePrefix + $Command)
}

function Sql([string]$Query) {
    (Compose -Command @('exec', '-T', 'db', 'psql', '-v', 'ON_ERROR_STOP=1',
        '-U', 'gateway', '-d', 'gateway_smoke', '-Atc', $Query) | Out-String).Trim()
}

function Health([string]$Path, [int]$Expected) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-WebRequest "$script:baseUrl/health/$Path" -SkipHttpErrorCheck -TimeoutSec 10
    $watch.Stop()
    if ([int]$response.StatusCode -ne $Expected) {
        throw "$Path returned $($response.StatusCode), expected $Expected"
    }
    $body = $response.Content | ConvertFrom-Json
    $status = if ($Expected -eq 503) { 'not_ready' } else { $Path }
    if ($body.status -ne $status) { throw "Unexpected $Path body" }
    [pscustomobject]@{ status = [int]$response.StatusCode; milliseconds = [math]::Round($watch.Elapsed.TotalMilliseconds, 2) }
}

function Wait-Ready([string]$Path = 'ready') {
    $until = [DateTime]::UtcNow.AddSeconds(60)
    do {
        try { return (Health -Path $Path -Expected 200) }
        catch { Start-Sleep -Milliseconds 500 }
    } while ([DateTime]::UtcNow -lt $until)
    throw "Timed out waiting for $Path"
}

function Pass([string]$Name, $Evidence) {
    $results.Add([pscustomobject]@{ name = $Name; passed = $true; evidence = $Evidence })
    Write-Host "PASS $Name : $($Evidence | ConvertTo-Json -Compress -Depth 4)"
}

try {
    if ((Run-Docker -Command @('info', '--format', '{{.OSType}}')).Trim() -ne 'linux') {
        throw 'Docker must use Linux containers'
    }
    # Never reuse or delete a pre-existing project's resources.
    $existing = Run-Docker -Command @('ps', '-aq', '--filter', "label=com.docker.compose.project=$project")
    $existingVolumes = Run-Docker -Command @('volume', 'ls', '-q', '--filter', "label=com.docker.compose.project=$project")
    if ($existing -or $existingVolumes) { throw 'Generated test project already exists' }

    if ($Port -eq 0) {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
        $listener.Start()
        $Port = $listener.LocalEndpoint.Port
        $listener.Stop()
    }
    $script:baseUrl = "http://127.0.0.1:$Port"
    $password = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
    # Host variables normally override --env-file; isolate them for this run.
    $smokeEnvironment = @{
        POSTGRES_PASSWORD = $password; POSTGRES_USER = 'gateway'
        POSTGRES_DB = 'gateway_smoke'; GATEWAY_PORT = "$Port"; GATEWAY_IMAGE = $Image
    }
    foreach ($name in $smokeEnvironment.Keys) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $smokeEnvironment[$name], 'Process')
    }
    [IO.File]::WriteAllText($envFile, "POSTGRES_PASSWORD=$password`nPOSTGRES_USER=gateway`nPOSTGRES_DB=gateway_smoke`nGATEWAY_PORT=$Port`nGATEWAY_IMAGE=$Image`n")
    $script:composePrefix = @('compose', '--env-file', $envFile, '-f', (Join-Path $repo 'compose.yaml'), '-p', $project)
    Write-Host "Isolated smoke project: $project at $script:baseUrl"

    $head = (Run-Docker -Command @('run', '--rm', $Image, 'python', '-c',
        'from agentic_gateway.database import migration_heads; heads = migration_heads(); assert len(heads) == 1; print(next(iter(heads)))') | Out-String).Trim()

    # e: Start only DB and API, deliberately bypassing migration dependencies.
    Compose -Command @('up', '-d', '--no-build', '--wait', 'db') | Write-Host
    Compose -Command @('up', '-d', '--no-build', '--no-deps', 'agentic-gateway-api') | Write-Host
    $null = Wait-Ready -Path 'live'
    Pass 'e_unmigrated_database' (Health -Path 'ready' -Expected 503)

    # a: Normal Compose startup runs migration before allowing app services.
    Compose -Command @('up', '-d', '--no-build') | Write-Host
    $ready = Wait-Ready
    $live = Health -Path 'live' -Expected 200
    $migrationId = (Compose -Command @('ps', '-aq', 'migrate') | Out-String).Trim()
    $migrationExit = (Run-Docker -Command @('inspect', '--format', '{{.State.ExitCode}}', $migrationId)).Trim()
    if ($migrationExit -ne '0') { throw 'Migration did not exit cleanly' }
    $revision = Sql -Query 'SELECT version_num FROM alembic_version'
    if ($revision -ne $head) { throw 'Database revision does not match image head' }
    Pass 'a_clean_database' @{ live = $live; ready = $ready; migration_exit = $migrationExit; revision = $revision }

    # b: Repeating upgrade must preserve the revision.
    Compose -Command @('run', '--rm', '--no-deps', 'migrate') | Write-Host
    if ((Sql -Query 'SELECT version_num FROM alembic_version') -ne $revision) { throw 'Revision changed on repeat' }
    Pass 'b_repeat_upgrade' @{ exit = 0; revision = $revision }

    Sql -Query "UPDATE alembic_version SET version_num = 'wrong_revision'" | Out-Null
    Pass 'wrong_revision' (Health -Path 'ready' -Expected 503)
    Sql -Query "UPDATE alembic_version SET version_num = '$head'" | Out-Null
    $null = Wait-Ready

    $apiId = (Compose -Command @('ps', '-q', 'agentic-gateway-api') | Out-String).Trim()
    $startedAt = (Run-Docker -Command @('inspect', '--format', '{{.State.StartedAt}}', $apiId)).Trim()
    # c: API remains alive while DB is stopped; readiness must fail promptly.
    Compose -Command @('stop', '--timeout', '10', 'db') | Write-Host
    $live = Health -Path 'live' -Expected 200
    $ready = Health -Path 'ready' -Expected 503
    if ($ready.milliseconds -ge 8000) { throw 'Readiness exceeded the 8-second smoke limit' }
    Pass 'c_database_stopped' @{ live = $live; ready = $ready }

    # d: Same API process recovers after database returns.
    Compose -Command @('start', 'db') | Write-Host
    $ready = Wait-Ready
    $currentStart = (Run-Docker -Command @('inspect', '--format', '{{.State.StartedAt}}', $apiId)).Trim()
    if ($currentStart -ne $startedAt) { throw 'API restarted during recovery' }
    Pass 'd_database_recovery' @{ ready = $ready; api_start_unchanged = $true }

    # f: Marker must survive DB container replacement BEFORE any migration rerun.
    $marker = [guid]::NewGuid().ToString('N')
    Sql -Query "CREATE TABLE runtime_smoke_marker (value text NOT NULL); INSERT INTO runtime_smoke_marker VALUES ('$marker')" | Out-Null
    $oldDbId = (Compose -Command @('ps', '-q', 'db') | Out-String).Trim()
    Compose -Command @('stop', '--timeout', '10', 'db') | Write-Host
    Compose -Command @('rm', '-f', 'db') | Write-Host
    Compose -Command @('up', '-d', '--no-build', '--no-deps', '--wait', 'db') | Write-Host
    $newDbId = (Compose -Command @('ps', '-q', 'db') | Out-String).Trim()
    if ($oldDbId -eq $newDbId) { throw 'Database container was not replaced' }
    if ((Sql -Query 'SELECT value FROM runtime_smoke_marker') -ne $marker) { throw 'Persistence marker lost' }
    if ((Sql -Query 'SELECT version_num FROM alembic_version') -ne $revision) { throw 'Migration revision lost' }
    $null = Wait-Ready
    Pass 'f_volume_persistence' @{ marker_preserved = $true; revision = $revision; container_replaced = $true; read_before_migration = $true }

    # g: Docker SIGTERM must produce a shutdown event and normal exit.
    $workerId = (Compose -Command @('ps', '-q', 'agentic-gateway-worker') | Out-String).Trim()
    Compose -Command @('stop', '--timeout', '10', 'agentic-gateway-worker') | Write-Host
    $workerExit = (Run-Docker -Command @('inspect', '--format', '{{.State.ExitCode}}', $workerId)).Trim()
    $workerLogs = Compose -Command @('logs', '--no-color', 'agentic-gateway-worker') | Out-String
    if ($workerExit -ne '0' -or $workerLogs -notmatch 'worker_started' -or $workerLogs -notmatch 'worker_stopped') {
        throw 'Worker did not shut down cleanly'
    }
    Pass 'g_worker_sigterm' @{ exit = 0; startup_event = $true; shutdown_event = $true }
}
catch {
    $failure = $_
    Write-Host "FAIL: $($_.Exception.Message)"
}
finally {
    if (Test-Path -LiteralPath $envFile) {
        try {
            Compose -Command @('down', '--volumes', '--remove-orphans', '--timeout', '10') | Write-Host
            $cleaned = $true
        }
        catch { if (-not $failure) { $failure = $_ }; Write-Host 'Test resource cleanup failed.' }
        Remove-Item -LiteralPath $envFile
    }
    $reportDir = Join-Path $repo 'reports/runtime'
    [IO.Directory]::CreateDirectory($reportDir) | Out-Null
    $reportPath = Join-Path $reportDir "$project.json"
    [ordered]@{
        project = $project; url = $script:baseUrl; image = $Image
        passed = ($null -eq $failure); resources_cleaned = $cleaned
        checks = $results.ToArray()
        failure = $(if ($failure) { $failure.Exception.Message } else { $null })
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding utf8
    Write-Host "Report: $reportPath"
    foreach ($name in $previousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
}
if ($failure) { throw $failure }
