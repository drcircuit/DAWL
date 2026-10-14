param([ValidateSet('start','stop','status')][string]$Action='status')
$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent $PSScriptRoot
$folder=Join-Path $repoRoot 'artifacts/public-site'
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$pidFile=Join-Path $folder 'publisher.pid'
$publisher=$null
if (Test-Path -LiteralPath $pidFile) {
    $number=[int](Get-Content -LiteralPath $pidFile)
    $publisher=Get-CimInstance Win32_Process -Filter "ProcessId=$number" -ErrorAction SilentlyContinue
    if ($publisher -and $publisher.CommandLine -notlike '*scripts/azure/public_site.py watch*') { throw 'PID belongs to another process' }
}
if ($Action -eq 'status') { if ($publisher) { 'Publisher running: '+$publisher.ProcessId } else { 'Publisher stopped' }; exit }
if ($Action -eq 'stop') { if ($publisher) { Stop-Process -Id $publisher.ProcessId }; exit }
if ($publisher) { 'Publisher already running'; exit }
$process=Start-Process -FilePath "$repoRoot/.venv/Scripts/python.exe" -ArgumentList @('-u','scripts/azure/public_site.py','watch') -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput "$folder/publisher.stdout.log" -RedirectStandardError "$folder/publisher.stderr.log"
$process.Id | Set-Content -LiteralPath $pidFile
'Publisher started: '+$process.Id
