# Desktop shortcut to dist\Wallet-beheer.exe, or Wallet-beheer.cmd if the exe is absent.
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$Desktop = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $ScriptDir
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

$exe = Join-Path $RepoRoot "dist\Wallet-beheer.exe"
$cmdDist = Join-Path $RepoRoot "dist\Wallet-beheer.cmd"
$cmdScript = Join-Path $ScriptDir "Wallet-beheer.cmd"
$icon = Join-Path $RepoRoot "assets\wallet-beheer.ico"

$target = $exe
if (-not (Test-Path -LiteralPath $exe)) {
    if (Test-Path -LiteralPath $cmdDist) {
        $target = $cmdDist
    } elseif (Test-Path -LiteralPath $cmdScript) {
        $target = $cmdScript
    } else {
        throw "Wallet-beheer.exe and Wallet-beheer.cmd are both missing"
    }
}

if (-not $Desktop) {
    $Desktop = [Environment]::GetFolderPath("Desktop")
}
if (-not $Desktop) {
    throw "Desktop folder not found"
}
New-Item -ItemType Directory -Force -Path $Desktop | Out-Null
$linkPath = Join-Path $Desktop "Wallet-beheer.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $RepoRoot
$shortcut.WindowStyle = 1
$shortcut.Description = "Wallet-beheer"
if (Test-Path -LiteralPath $icon) {
    $shortcut.IconLocation = $icon
} elseif ($target -like "*.exe") {
    $shortcut.IconLocation = "$target,0"
}
$shortcut.Save()
Write-Host "Shortcut: $linkPath"
Write-Host "Target: $target"
