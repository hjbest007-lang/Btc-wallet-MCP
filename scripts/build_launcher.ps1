# Build dist\Wallet-beheer.exe and copy the Wallet-beheer.cmd fallback.
# The exe does not bundle wallet_mcp or crypto libraries. It starts the venv:
#   python.exe -m wallet_mcp launch
# PyInstaller is the dev extra in pyproject.toml.
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $ScriptDir
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

$Entry = Join-Path $ScriptDir "launcher_entry.py"
$CmdSource = Join-Path $ScriptDir "Wallet-beheer.cmd"
$Icon = Join-Path $RepoRoot "assets\wallet-beheer.ico"
$Dist = Join-Path $RepoRoot "dist"
$Work = Join-Path $RepoRoot "build\launcher"

if (-not (Test-Path -LiteralPath $Entry)) {
    throw "missing $Entry"
}
$entryText = Get-Content -LiteralPath $Entry -Raw
if ($entryText -match '(?m)^\s*(import|from)\s+(wallet_mcp|btc_wallet_mcp|bdkpython|cryptography|solders|eth_account|xrpl|embit)\b') {
    throw "launcher_entry.py must not import wallet_mcp or crypto libraries"
}

New-Item -ItemType Directory -Force -Path $Dist | Out-Null
Copy-Item -LiteralPath $CmdSource -Destination (Join-Path $Dist "Wallet-beheer.cmd") -Force

function Resolve-Builder {
    param([string]$Explicit, [string]$Root)
    if ($Explicit) {
        return $Explicit
    }
    $candidates = @(
        (Join-Path $Root ".venv\Scripts\python.exe"),
        (Join-Path $env:USERPROFILE "Btc-wallet-MCP\.venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return $pyLauncher.Source
    }
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        return $pythonCmd.Source
    }
    throw "python.exe not found. Pass -Python or create the .venv."
}

$Builder = Resolve-Builder -Explicit $Python -Root $RepoRoot
Write-Host "Using $Builder"
& $Builder -m pip install --disable-pip-version-check "pyinstaller>=6"
if ($LASTEXITCODE -ne 0) {
    throw "pip install pyinstaller failed"
}

$pyArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--console",
    "--noupx",
    "--name", "Wallet-beheer",
    "--distpath", $Dist,
    "--workpath", $Work,
    "--specpath", $Work,
    "--exclude-module", "wallet_mcp",
    "--exclude-module", "btc_wallet_mcp",
    "--exclude-module", "bdkpython",
    "--exclude-module", "cryptography",
    "--exclude-module", "solders",
    "--exclude-module", "eth_account",
    "--exclude-module", "xrpl",
    "--exclude-module", "embit",
    "--exclude-module", "coincurve",
    "--exclude-module", "nacl"
)
if (Test-Path -LiteralPath $Icon) {
    $pyArgs += @("--icon", $Icon)
}
$pyArgs += $Entry

& $Builder @pyArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed"
}

$exe = Join-Path $Dist "Wallet-beheer.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "missing $exe"
}
Write-Host "Built $exe"
Write-Host "Fallback $(Join-Path $Dist 'Wallet-beheer.cmd')"
