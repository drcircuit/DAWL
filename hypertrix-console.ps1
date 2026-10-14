param([string]$Endpoint='http://127.0.0.1:8882/api/status',[switch]$Once)
$ErrorActionPreference='Stop'
$python=Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Create the project virtual environment and install requirements-console.txt first.' }
$consoleArguments=@('-X','utf8',(Join-Path $PSScriptRoot 'scripts/hypertrix_tui.py'),'--endpoint',$Endpoint)
if ($Once) { $consoleArguments+='--once' }
& $python @consoleArguments
