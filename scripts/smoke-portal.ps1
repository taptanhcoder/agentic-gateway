[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$portalRepo = Split-Path $PSScriptRoot -Parent
$portalProject = 'ag-portal-smoke-' + [guid]::NewGuid().ToString('N').Substring(0,10)
$portalEnvFile = Join-Path $portalRepo ".env.$portalProject"
$portalPrevious = @{}
$portalChecks = [Collections.Generic.List[string]]::new()
function Portal-Port {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0)
    $listener.Start()
    try { return $listener.LocalEndpoint.Port } finally { $listener.Stop() }
}
function Portal-Docker([string[]]$Arguments) {
    & docker @Arguments
    if($LASTEXITCODE -ne 0) { throw 'Portal smoke Docker command failed.' }
}
function Portal-Assert($Condition,[string]$Name) {
    if(-not $Condition) { throw "Portal smoke failed: $Name" }
    $portalChecks.Add($Name)
    Write-Host "PASS $Name"
}
try {
    $portalPassword=[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
    $portalOperator=[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
    $portalPort=Portal-Port
    $portalApiPort=Portal-Port
    $portalValues=@{POSTGRES_DB='registry_test';POSTGRES_USER='gateway';POSTGRES_PASSWORD=$portalPassword;OPERATOR_API_KEY=$portalOperator;GATEWAY_IMAGE='agentic-gateway:dev';GATEWAY_PORT=$portalApiPort;PORTAL_PORT=$portalPort;PORTAL_COOKIE_SECURE='0';NVIDIA_API_KEY='';SILICONFLOW_CN_CATALOG_API_KEY='';QUALIFICATION_LIVE_MAX_PROBES='0';QUALIFICATION_LIVE_ACCOUNT_ID='';QUALIFICATION_LIVE_ACK_ACCOUNT_TERMS='0'}
    foreach($key in $portalValues.Keys){$portalPrevious[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,[string]$portalValues[$key],'Process')}
    [IO.File]::WriteAllLines($portalEnvFile,($portalValues.GetEnumerator()|ForEach-Object {"$($_.Key)=$($_.Value)"}))
    $portalCompose=@('compose','--env-file',$portalEnvFile,'-f',(Join-Path $portalRepo 'compose.yaml'),'-p',$portalProject)
    Write-Host "Isolated Portal smoke: $portalProject"
    Portal-Docker ($portalCompose+@('up','-d','--no-build','--wait','db','migrate','agentic-gateway-api','agentic-gateway-worker','agentic-gateway-portal'))
    $portalBase="http://127.0.0.1:$portalPort"
    $portalOrigin=@{Origin=$portalBase}
    $portalPage=Invoke-WebRequest $portalBase -TimeoutSec 10
    Portal-Assert ($portalPage.StatusCode -eq 200) 'Frontend HTML served'
    foreach($match in [regex]::Matches($portalPage.Content,'(?:src|href)="(/assets/[^\"]+)"')){
        $asset=Invoke-WebRequest ($portalBase+$match.Groups[1].Value) -TimeoutSec 10
        Portal-Assert ($asset.StatusCode -eq 200) 'Compiled asset served'
    }
    $denied=Invoke-WebRequest "$portalBase/portal-api/backend/admin/portal/summary" -SkipHttpErrorCheck
    Portal-Assert ($denied.StatusCode -eq 401) 'Unauthenticated admin denied'
    $login=Invoke-WebRequest "$portalBase/portal-api/session" -Method Post -Headers $portalOrigin -ContentType application/json -Body (@{credential=$portalOperator}|ConvertTo-Json) -SessionVariable portalSession
    Portal-Assert ($login.StatusCode -eq 200 -and ($login.Headers['Set-Cookie'] -join '') -match 'HttpOnly') 'Operator login establishes HttpOnly session'
    $summary=Invoke-RestMethod "$portalBase/portal-api/backend/admin/portal/summary" -WebSession $portalSession
    Portal-Assert ($summary.capabilities.source_sync -and -not $summary.capabilities.public_inference) 'BFF reads real backend capabilities'
    $source=Invoke-RestMethod "$portalBase/portal-api/backend/admin/sources" -Method Post -Headers $portalOrigin -ContentType application/json -Body '{"id":"fixture/portal-smoke","fixture_name":"catalog-a"}' -WebSession $portalSession
    Portal-Assert ($source.id -eq 'fixture/portal-smoke') 'Source registration persists through BFF'
    $job=Invoke-RestMethod "$portalBase/portal-api/backend/admin/sources/fixture%2Fportal-smoke/sync" -Method Post -Headers $portalOrigin -ContentType application/json -Body '{"revision":"r1"}' -WebSession $portalSession
    $portalDeadline=[DateTime]::UtcNow.AddSeconds(20)
    do {Start-Sleep -Milliseconds 200;$state=Invoke-RestMethod "$portalBase/portal-api/backend/admin/jobs/$($job.job_id)" -WebSession $portalSession} while($state.status -in @('queued','running') -and [DateTime]::UtcNow -lt $portalDeadline)
    Portal-Assert ($state.status -eq 'succeeded') 'API to worker to PostgreSQL sync completed'
    $offerings=Invoke-RestMethod "$portalBase/portal-api/backend/admin/offerings" -WebSession $portalSession
    Portal-Assert ($offerings.pagination.total -gt 0) 'Published offerings returned via BFF'
    $offeringId=$offerings.data[0].id
    $proof=Invoke-RestMethod "$portalBase/portal-api/backend/admin/offerings/$offeringId/evidence" -WebSession $portalSession
    Portal-Assert ($proof.inference_eligible -eq $false) 'Evidence never grants inference permission'
    $report=Invoke-RestMethod "$portalBase/portal-api/backend/admin/portal/reports?days=7&mode=fixture" -WebSession $portalSession
    Portal-Assert ($report.providers.Count -gt 0 -and $report.totals.attempts -eq 0 -and $null -eq $report.totals.total_tokens) 'Report reads current inventory with unknown unobserved usage'
    $reportProvider=[Uri]::EscapeDataString($report.providers[0].id)
    $providerReport=Invoke-RestMethod "$portalBase/portal-api/backend/admin/portal/reports?days=7&mode=live&provider=$reportProvider" -WebSession $portalSession
    Portal-Assert ($null -ne $providerReport.details -and $providerReport.filters.mode -eq 'live') 'Provider detail report is available through authenticated BFF'
    $live=Invoke-WebRequest "$portalBase/portal-api/backend/admin/qualifications" -Method Post -Headers $portalOrigin -ContentType application/json -Body '{"deployment_id":"missing","mode":"live"}' -WebSession $portalSession -SkipHttpErrorCheck
    Portal-Assert ($live.StatusCode -eq 403) 'Portal blocks live qualification'
    $csrf=Invoke-WebRequest "$portalBase/portal-api/backend/admin/sources" -Method Post -Headers @{Origin='https://untrusted.test'} -ContentType application/json -Body '{}' -WebSession $portalSession -SkipHttpErrorCheck
    Portal-Assert ($csrf.StatusCode -eq 403) 'Cross-origin write blocked'
    Invoke-RestMethod "$portalBase/portal-api/session" -Method Delete -Headers $portalOrigin -ContentType application/json -Body '{}' -WebSession $portalSession | Out-Null
    $after=Invoke-WebRequest "$portalBase/portal-api/backend/admin/portal/summary" -WebSession $portalSession -SkipHttpErrorCheck
    Portal-Assert ($after.StatusCode -eq 401) 'Logout invalidates operator session'
    $reportDirectory=Join-Path $portalRepo 'reports/runtime'
    [IO.Directory]::CreateDirectory($reportDirectory)|Out-Null
    @{project=$portalProject;checks=$portalChecks.ToArray();passed=$true;live_inference_calls=0;timestamp=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $reportDirectory "$portalProject.json")
}
finally {
    try { if(Test-Path -LiteralPath $portalEnvFile){Portal-Docker ($portalCompose+@('down','--volumes','--remove-orphans','--timeout','10'))} }
    finally {
        if(Test-Path -LiteralPath $portalEnvFile){Remove-Item -LiteralPath $portalEnvFile -Force}
        foreach($key in $portalPrevious.Keys){[Environment]::SetEnvironmentVariable($key,$portalPrevious[$key],'Process')}
    }
}
