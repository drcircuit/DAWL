param([ValidateSet('start','stop','status')][string]$Action='status')
$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent $PSScriptRoot
$folder=Join-Path $repoRoot 'artifacts/console'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$pidFile=Join-Path $folder 'collector.pid'
$collector=$null
if (Test-Path -LiteralPath $pidFile) {
    $number=[int](Get-Content -LiteralPath $pidFile)
    $collector=Get-CimInstance Win32_Process -Filter "ProcessId=$number" -ErrorAction SilentlyContinue
    if ($collector -and $collector.CommandLine -notlike '*scripts/local_testnet_telemetry.py*') { throw 'PID belongs to another process' }
}
if ($Action -eq 'status') { if ($collector) { 'Collector running: '+$collector.ProcessId } else { 'Collector stopped' }; exit }
if ($Action -eq 'stop') { if ($collector) { Stop-Process -Id $collector.ProcessId }; exit }
if ($collector) { 'Collector already running'; exit }
$process=Start-Process -FilePath wsl.exe -ArgumentList @('-d','kali-linux','-u','root','--exec','python3','scripts/local_testnet_telemetry.py','--root','/var/lib/dawl/oracle-training-003/state','--exchange','/var/lib/dawl/oracle-training-003/console-telemetry','--run','003') -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput "$folder/collector.stdout.log" -RedirectStandardError "$folder/collector.stderr.log"
$process.Id | Set-Content -LiteralPath $pidFile
'Collector started: '+$process.Id
