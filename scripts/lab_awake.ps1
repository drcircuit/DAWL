param([string]$Distribution = 'kali-linux', [string]$Root = '/var/lib/dawl/seed-lab-001', [int]$MaximumHours = 48, [string]$CompletionFile = 'lab-finished.json')
$ErrorActionPreference = 'Stop'
if ($CompletionFile -notmatch '^[a-z0-9-]+\.json$') { throw 'Invalid completion filename' }
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class LabIdleGuard {
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern uint SetThreadExecutionState(uint flags);
}
'@
$deadline = (Get-Date).AddHours($MaximumHours)
try {
    if ([LabIdleGuard]::SetThreadExecutionState([uint32]2147483649) -eq 0) { throw 'Unable to set idle-sleep guard' }
    Write-Output ('Idle-sleep guard active until completion or '+$deadline.ToString('s'))
    while ((Get-Date) -lt $deadline) {
        & wsl.exe -d $Distribution -u root --exec test -f "$Root/$CompletionFile"
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 20
    }
} finally {
    [void][LabIdleGuard]::SetThreadExecutionState([uint32]2147483648)
}
