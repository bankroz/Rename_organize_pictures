param([string]$ConfigPath = '')
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    if (-not $ConfigPath) { $ConfigPath = Join-Path $ProjectRoot 'patterns.json' }
    $resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
    python -m PyInstaller packaging/photo_renamer_gui.spec --noconfirm --distpath dist --workpath build/desktop
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed' }
    Copy-Item -LiteralPath $resolvedConfig -Destination 'dist/photo_renamer_desktop/patterns.json'
    Write-Output 'Desktop output: dist/photo_renamer_desktop/photo_renamer_desktop.exe'
} finally {
    Pop-Location
}
