param(
    [ValidateSet('Doctor','Run','Audit','Fetch','Network','Ddp','DdpTest','Provider','Mesh','Studio','Status','Stop')][string]$Action='Doctor',
    [string]$Distribution='Ubuntu',
    [string]$NodeName=($env:COMPUTERNAME).ToLower(),
    [string]$Storage='/var/lib/dawl/home-lab',
    [string]$ImageArchive,
    [switch]$Install,
    [switch]$Cpu,
    [string[]]$Task=@()
)
$ErrorActionPreference='Stop'
if ($NodeName -notmatch '^[a-z0-9][a-z0-9-]{0,35}$') { throw 'Use a short lowercase node name.' }
if ($Storage -notmatch '^/var/lib/dawl/[a-zA-Z0-9/_-]+$' -or $Storage.Contains('..')) { throw 'Storage must be a native path beneath /var/lib/dawl/.' }
$bundle=Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $bundle 'release-manifest.json'))) {
    throw 'Use the extracted release artifact, which includes release-manifest.json.'
}
$manifest=Get-Content -LiteralPath (Join-Path $bundle 'release-manifest.json') -Raw | ConvertFrom-Json
foreach ($file in $manifest.files) {
    if ($file.path -match '(^/|\\|(^|/)\.\.(/|$)|:)') { throw 'Unsafe release path.' }
    $path=Join-Path $bundle $file.path
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower() -ne $file.sha256) { throw "Release file differs: $($file.path)" }
}
$converted=& wsl.exe -d $Distribution -u root --exec wslpath -a -u $bundle.Replace('\','/')
if ($LASTEXITCODE -ne 0) { throw 'Start/install the named WSL2 distribution first.' }
$linuxBundle=($converted -join '').Trim()
$linuxArchive='-'
if ($ImageArchive) {
    $archive=(Resolve-Path -LiteralPath $ImageArchive).Path
    if ($manifest.runtime.archive_sha256 -and (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower() -ne $manifest.runtime.archive_sha256) { throw 'Runtime archive hash differs.' }
    $linuxArchive=(& wsl.exe -d $Distribution -u root --exec wslpath -a -u $archive.Replace('\','/')).Trim()
}
$installFlag=if($Install){'yes'}else{'no'}
$cpuFlag=if($Cpu){'yes'}else{'no'}
$guarded=$Action -in @('Run','Audit','Ddp','Fetch')
if ($guarded) {
    if (-not ('HypertrixIdleGuard' -as [type])) {
        Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class HypertrixIdleGuard {
  [DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);
}
'@
    }
    if ([HypertrixIdleGuard]::SetThreadExecutionState([uint32]2147483649) -eq 0) { throw 'Unable to request idle-sleep prevention.' }
}
try {
    & wsl.exe -d $Distribution -u root --exec sh "$linuxBundle/lab/lab.sh" $Action.ToLower() $linuxBundle $Storage $NodeName $linuxArchive $installFlag $cpuFlag @Task
    if ($LASTEXITCODE -ne 0) { throw "Lab command failed with exit code $LASTEXITCODE" }
} finally {
    if ($guarded) { [void][HypertrixIdleGuard]::SetThreadExecutionState([uint32]2147483648) }
}
