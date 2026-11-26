[CmdletBinding()]
param(
    [ValidateSet('r1','r2','r3','last-page-timeout')]
    [string]$Revision = 'r1',
    [string]$Source = 'fixture/catalog-a',
    [string]$BaseUrl = 'http://127.0.0.1:8000'
)
$ErrorActionPreference = 'Stop'
$operatorKey = $env:OPERATOR_API_KEY
if (-not $operatorKey) {
    $envPath = Join-Path (Split-Path $PSScriptRoot -Parent) '.env'
    $line = Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^OPERATOR_API_KEY=' } | Select-Object -Last 1
    if ($line) { $operatorKey = ($line -replace '^OPERATOR_API_KEY=', '').Trim().Trim('"').Trim("'") }
}
if (-not $operatorKey) { throw 'Configure OPERATOR_API_KEY in .env or the process environment.' }
$headers = @{ Authorization = "Bearer $operatorKey" }
$job = Invoke-RestMethod -Method Post -Uri "$BaseUrl/admin/sources/$Source/sync" -Headers $headers `
    -ContentType 'application/json' -Body (@{revision=$Revision} | ConvertTo-Json) -TimeoutSec 10
$until = [DateTime]::UtcNow.AddSeconds(30)
do {
    $state = Invoke-RestMethod "$BaseUrl/admin/jobs/$($job.job_id)" -Headers $headers -TimeoutSec 10
    if ($state.status -notin @('queued','running')) { $state | ConvertTo-Json -Depth 8; return }
    Start-Sleep -Milliseconds 200
} while ([DateTime]::UtcNow -lt $until)
throw "Job $($job.job_id) remains $($state.status); poll GET /admin/jobs/$($job.job_id)."
