param([ValidateSet('start','restart','status')][string]$Action='status')
$ErrorActionPreference='Stop'
$labRoot=Split-Path -Parent $PSScriptRoot
$listener=Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8885 -State Listen -ErrorAction SilentlyContinue
if ($Action -eq 'status') {
    if ($listener) { Invoke-RestMethod http://127.0.0.1:8885/api/status | Select-Object api_reserved_usd,api_limit_usd }
    else { Write-Output 'Trust Lab is not listening.' }
    exit
}
if ($listener) {
    $process=Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if ($process.CommandLine -notlike '*scripts/trust_lab.py*') { throw 'Unexpected process on port 8885' }
    if ($Action -eq 'start') { Write-Output 'Trust Lab already running.'; exit }
    Stop-Process -Id $listener.OwningProcess
}
New-Item -ItemType Directory -Force -Path "$labRoot/artifacts/trust-lab-001" | Out-Null
$labArguments=@('scripts/trust_lab.py','--pilot','\\wsl.localhost\kali-linux\var\lib\dawl\finetune-economics-001')
$studyDocument=Join-Path $labRoot 'research/trust-study-model-strict-002.json'
if (Test-Path -LiteralPath $studyDocument) {
    $studyPilot='\\wsl.localhost\kali-linux\var\lib\dawl\finetune-strict-002'
    $studyConfig=Get-Content -LiteralPath $studyDocument -Raw | ConvertFrom-Json
    $studyReleaseHash=(Get-FileHash -LiteralPath "$studyPilot/release.json" -Algorithm SHA256).Hash.ToLower()
    if ($studyReleaseHash -ne $studyConfig.release_sha256) { throw 'Study release differs from frozen documentation.' }
    $labArguments+=@('--study-pilot',$studyPilot,'--study-documentation','research/trust-study-model-strict-002.json')
}
Start-Process -FilePath "$labRoot/.venv/Scripts/python.exe" -ArgumentList $labArguments -WorkingDirectory $labRoot -WindowStyle Hidden -RedirectStandardOutput "$labRoot/artifacts/trust-lab-001/server.stdout.log" -RedirectStandardError "$labRoot/artifacts/trust-lab-001/server.stderr.log" -PassThru | Select-Object Id
