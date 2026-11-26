# API + actual scheduler entry points on an isolated fixture-only Compose stack.
[CmdletBinding()]
param([string]$Image = 'agentic-gateway:dev')
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repo = Split-Path $PSScriptRoot -Parent
$project = 'ag-catalog-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$envFile = Join-Path $repo ".env.$project"
$previous = @{}
$checks = [Collections.Generic.List[object]]::new()
$failure = $null
$cleaned = $false
$examples = @{}

function Invoke-CatalogDocker([string[]]$Command) {
    $output = & docker @Command 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Docker failed: $($output -join [Environment]::NewLine)" }
    $output | ForEach-Object { $_.ToString() }
}
function Compose([string[]]$Command) { Invoke-CatalogDocker -Command ($script:compose + $Command) }
function Api([string]$Path, [string]$Method = 'Get', $Body = $null) {
    $parameters = @{Uri="$script:apiRoot$Path"; Method=$Method; Headers=$script:headers; TimeoutSec=10}
    if ($null -ne $Body) { $parameters.ContentType='application/json'; $parameters.Body=$Body | ConvertTo-Json -Depth 10 }
    Invoke-RestMethod @parameters
}
function Source {
    (Api -Path '/admin/sources').data | Where-Object source_id -eq 'fixture/catalog-a'
}
function Wait-Job([string]$Id) {
    $until = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $job = Api -Path "/admin/jobs/$Id"
        if ($job.status -notin @('queued','running')) { return $job }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $until)
    throw "Job $Id did not finish"
}
function Pass([string]$Name) { $checks.Add(@{name=$Name;passed=$true}); Write-Host "PASS $Name" }

try {
    if ((Invoke-CatalogDocker -Command @('info','--format','{{.OSType}}')).Trim() -ne 'linux') { throw 'Linux engine required' }
    $existing = Invoke-CatalogDocker -Command @('ps','-aq','--filter',"label=com.docker.compose.project=$project")
    $volumes = Invoke-CatalogDocker -Command @('volume','ls','-q','--filter',"label=com.docker.compose.project=$project")
    if ($existing -or $volumes) { throw 'Test project already exists' }
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0)
    $listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop()
    $password = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
    $key = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
    $values = @{POSTGRES_DB='catalog_smoke';POSTGRES_USER='gateway';POSTGRES_PASSWORD=$password;
        GATEWAY_PORT="$port";GATEWAY_IMAGE=$Image;OPERATOR_API_KEY=$key;SILICONFLOW_CN_CATALOG_API_KEY=''}
    foreach($name in $values.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name,'Process')
        [Environment]::SetEnvironmentVariable($name,$values[$name],'Process')
    }
    $lines = foreach($name in $values.Keys){ "$name=$($values[$name])" }
    [IO.File]::WriteAllText($envFile, ($lines -join "`n") + "`n")
    $script:compose = @('compose','--env-file',$envFile,'-f',(Join-Path $repo 'compose.yaml'),'-p',$project)
    $script:apiRoot = "http://127.0.0.1:$port"
    $script:headers = @{Authorization="Bearer $key"}
    Write-Host "Fixture catalog smoke: $project at $script:apiRoot"
    Compose -Command @('up','-d','--no-build','agentic-gateway-api','agentic-gateway-worker') | Write-Host
    $until = [DateTime]::UtcNow.AddSeconds(40)
    do {
        try { $ready = Api -Path '/health/ready'; break }
        catch { Start-Sleep -Milliseconds 300 }
    } while ([DateTime]::UtcNow -lt $until)
    if ($ready.status -ne 'ready') { throw 'API not ready' }

    $job = Api -Path '/admin/sources/fixture/catalog-a/sync' -Method Post -Body @{revision='r1'}
    $success = Wait-Job -Id $job.job_id
    if ($success.status -ne 'succeeded') { throw 'Initial fixture sync failed' }
    $first = Source
    $snapshot = $first.active_snapshot_id
    $before = Api -Path '/admin/offerings?source=fixture%2Fcatalog-a'
    if (-not $snapshot -or $before.data.Count -ne 2) { throw 'Initial snapshot missing' }
    Pass 'initial_manual_success'

    $future = [DateTimeOffset]::UtcNow.AddHours(2).ToString('o')
    $null = Api -Path '/admin/sources/fixture/catalog-a/schedule' -Method Put -Body @{
        interval_seconds=3600;next_sync_at=$future;revision='last-page-timeout'}
    $job = Api -Path '/admin/sources/fixture/catalog-a/sync' -Method Post -Body @{revision='last-page-timeout'}
    $failed = Wait-Job -Id $job.job_id
    $source = Source
    $after = Api -Path '/admin/offerings?source=fixture%2Fcatalog-a'
    if ($failed.status -ne 'failed' -or $source.active_snapshot_id -ne $snapshot -or
        ($before | ConvertTo-Json -Depth 12 -Compress) -ne ($after | ConvertTo-Json -Depth 12 -Compress)) {
        throw 'Failure replaced active catalog'
    }
    if ($source.last_successful_run_id -ne $success.sync_run_id -or -not $source.next_sync_at) { throw 'Success/schedule information lost' }
    Pass 'latest_failure_keeps_success_snapshot_and_next_schedule'

    Compose -Command @('stop','--timeout','10','agentic-gateway-worker') | Write-Host
    $clock = [DateTimeOffset]::UtcNow
    $null = Api -Path '/admin/sources/fixture/catalog-a/schedule' -Method Put -Body @{
        interval_seconds=3600;next_sync_at=$clock.AddMinutes(-1).ToString('o');revision='last-page-timeout'}
    $once = @('run','--rm','--no-deps','agentic-gateway-scheduler','python','-m','agentic_gateway.scheduler',
        '--once','--now',$clock.ToString('o'))
    Compose -Command $once | Write-Host
    $scheduled = Source
    $scheduledId = $scheduled.latest_job.job_id
    if ($scheduled.latest_job.trigger -ne 'scheduled' -or $scheduled.latest_job.status -ne 'queued') { throw 'Scheduler did not queue job' }
    Compose -Command $once | Write-Host
    if ((Source).latest_job.job_id -ne $scheduledId) { throw 'Restart duplicated schedule period' }
    Pass 'actual_scheduler_once_and_restart_are_idempotent'

    Compose -Command @('start','agentic-gateway-worker') | Write-Host
    $completed = Wait-Job -Id $scheduledId
    $source = Source
    $run = Api -Path "/admin/sync-runs/$($completed.sync_run_id)"
    if ($completed.status -ne 'failed' -or $run.trigger -ne 'scheduled' -or $run.records_staged -ne 1 -or
        $run.records_published -ne 0 -or $null -ne $run.diff -or $null -ne $run.snapshot_id -or $source.active_snapshot_id -ne $snapshot) {
        throw 'Scheduled failed run contract violated'
    }
    Pass 'scheduled_failure_reports_staging_without_published_diff'
    $examples = @{sources_response=(Api -Path '/admin/sources');offerings_response=(Api -Path '/admin/offerings?source=fixture%2Fcatalog-a');
        failed_run_response=$run;success_run_response=(Api -Path "/admin/sync-runs/$($success.sync_run_id)")}

    Compose -Command @('up','-d','--no-build','agentic-gateway-scheduler') | Write-Host
    $until = [DateTime]::UtcNow.AddSeconds(15)
    do {
        $logs = Compose -Command @('logs','--no-color','agentic-gateway-scheduler') | Out-String
        if ($logs -match 'scheduler_started') { break }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $until)
    Compose -Command @('stop','--timeout','10','agentic-gateway-scheduler') | Write-Host
    $schedulerId = (Compose -Command @('ps','-aq','agentic-gateway-scheduler') | Out-String).Trim()
    $exitCode = (Invoke-CatalogDocker -Command @('inspect','--format','{{.State.ExitCode}}',$schedulerId)).Trim()
    $logs = Compose -Command @('logs','--no-color','agentic-gateway-scheduler') | Out-String
    if ($exitCode -ne '0' -or $logs -notmatch 'scheduler_started' -or $logs -notmatch 'scheduler_stopped') { throw 'Scheduler SIGTERM failed' }
    Pass 'scheduler_service_sigterm_exit_zero'
}
catch { $failure = $_; Write-Host "FAIL: $($_.Exception.Message)" }
finally {
    try {
        if (Test-Path -LiteralPath $envFile) {
            try { Compose -Command @('down','--volumes','--remove-orphans','--timeout','10') | Write-Host; $cleaned=$true }
            catch { if (-not $failure) { $failure=$_ }; Write-Host 'Test cleanup failed' }
            finally { Remove-Item -LiteralPath $envFile -Force }
        }
        $reportDir = Join-Path $repo 'reports/runtime'
        [IO.Directory]::CreateDirectory($reportDir) | Out-Null
        $path = Join-Path $reportDir "$project.json"
        @{project=$project;mode='fixture';passed=($null -eq $failure);resources_cleaned=$cleaned;
            checks=$checks.ToArray();examples=$examples;failure=$(if($failure){$failure.Exception.Message}else{$null})} |
            ConvertTo-Json -Depth 24 | Set-Content -LiteralPath $path -Encoding utf8
        Write-Host "Report: $path"
    }
    finally { foreach($name in $previous.Keys){ [Environment]::SetEnvironmentVariable($name,$previous[$name],'Process') } }
}
if ($failure) { throw $failure }
