# Dedicated PostgreSQL project; never resets the development database.
[CmdletBinding()]
param([string]$TestPath = 'tests', [string]$Image = 'agentic-gateway:test')
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repo = Split-Path $PSScriptRoot -Parent
$project = 'ag-registry-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$envFile = Join-Path $repo ".env.$project"
$testContainer = "$project-tests"
$previous = @{}

function Invoke-RegistryDocker([string[]]$Command) {
    & docker @Command
    if ($LASTEXITCODE -ne 0) { throw 'Docker command failed; see output above.' }
}

try {
    $password = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
    $values = @{ POSTGRES_DB='registry_test'; POSTGRES_USER='gateway'; POSTGRES_PASSWORD=$password; GATEWAY_IMAGE=$Image }
    foreach($name in $values.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process')
    }
    [IO.File]::WriteAllText($envFile, "POSTGRES_DB=registry_test`nPOSTGRES_USER=gateway`nPOSTGRES_PASSWORD=$password`nDB_HOST=db`nREGISTRY_TEST_DATABASE=isolated`n")
    $compose = @('compose','--env-file',$envFile,'-f',(Join-Path $repo 'compose.yaml'),'-p',$project)
    Write-Host "Isolated PostgreSQL tests: $project"
    Invoke-RegistryDocker -Command ($compose + @('up','-d','--no-build','--wait','db'))
    $reportDir = Join-Path $repo 'reports/runtime'
    [IO.Directory]::CreateDirectory($reportDir) | Out-Null
    # Write inside the container, then copy out: Linux bind mounts retain host
    # ownership and are not writable by the non-root image UID on GitHub runners.
    Invoke-RegistryDocker -Command @('run','--name',$testContainer,'--network',"${project}_default",'--env-file',$envFile,
        $Image,'python','-m','pytest','-p','no:cacheprovider','-v',$TestPath,'--junitxml=/tmp/registry-tests.xml')
    Write-Host "Report: $reportDir/$project.xml"
}
finally {
    try {
        $existingTest = & docker ps -aq --filter "name=^/$testContainer$"
        if ($LASTEXITCODE -eq 0 -and $existingTest) {
            & docker cp "${testContainer}:/tmp/registry-tests.xml" (Join-Path $reportDir "$project.xml")
            if ($LASTEXITCODE -ne 0) { Write-Warning 'Test report was not produced; see test output.' }
            & docker rm -f $testContainer | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Could not remove this run test container.' }
        }
        if(Test-Path -LiteralPath $envFile) {
            try { Invoke-RegistryDocker -Command ($compose + @('down','--volumes','--remove-orphans','--timeout','10')) }
            finally { Remove-Item -LiteralPath $envFile -Force }
        }
    }
    finally {
        foreach($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    }
}
