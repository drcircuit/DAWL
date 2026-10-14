param([ValidateSet('start','stop','status')][string]$Action = 'start')
$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$linuxRepo = (& wsl -d kali-linux --exec wslpath -a $repoPath).Trim()
& wsl -d kali-linux -u root --exec sh "$linuxRepo/scripts/scratch_playground.sh" $Action
if ($LASTEXITCODE -ne 0) { throw 'Model playground command failed' }
