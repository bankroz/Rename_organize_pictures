$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    $Version = (Get-Content -LiteralPath VERSION -Raw).Trim()
    $PackageDir = Join-Path $ProjectRoot 'dist/photo_renamer_desktop'
    if (-not (Test-Path -LiteralPath "$PackageDir/photo_renamer_desktop.exe")) { throw 'Build the desktop application first' }
    Copy-Item README.md,CHANGELOG.md,THIRD_PARTY_NOTICES.md,VERSION -Destination $PackageDir
    New-Item -ItemType Directory -Force "$PackageDir/docs" | Out-Null
    Copy-Item -Path 'docs/*' -Destination "$PackageDir/docs" -Recurse -Force
    Copy-Item -LiteralPath CONTRIBUTING.md -Destination $PackageDir
    New-Item -ItemType Directory -Force "$PackageDir/licenses" | Out-Null
    $LicenseJson = python -c "import importlib.metadata as m,json; names=['PySide6_Essentials','shiboken6','Pillow','PyInstaller','rich','pygments','defusedxml']; print(json.dumps([{'package':n,'source':str(m.distribution(n).locate_file(f)),'name':str(f).replace('/','_')} for n in names for f in (m.distribution(n).files or []) if any(s in str(f).lower() for s in ['license','copying']) and str(f).lower().endswith(('.txt','.md','.rst','license','copying'))]))"
    if ($LASTEXITCODE -ne 0) { throw 'Unable to collect dependency licenses' }
    $LicenseFiles = $LicenseJson | ConvertFrom-Json
    foreach ($LicenseFile in $LicenseFiles) {
        if (Test-Path -LiteralPath $LicenseFile.source -PathType Leaf) {
            Copy-Item -LiteralPath $LicenseFile.source -Destination "$PackageDir/licenses/$($LicenseFile.package)_$($LicenseFile.name)"
        }
    }
    $PythonLicense = python -c "import sys; from pathlib import Path; print(Path(sys.base_prefix)/'LICENSE.txt')"
    if (Test-Path -LiteralPath $PythonLicense) { Copy-Item -LiteralPath $PythonLicense -Destination "$PackageDir/licenses/Python-LICENSE.txt" }
    $ProbeRoot = Split-Path -Parent (Split-Path -Parent (Get-Command ffprobe).Source)
    foreach ($LicenseName in @('LICENSE','README.txt')) {
        $ProbeLicense = Join-Path $ProbeRoot $LicenseName
        if (Test-Path -LiteralPath $ProbeLicense -PathType Leaf) {
            Copy-Item -LiteralPath $ProbeLicense -Destination "$PackageDir/licenses/FFmpeg-$LicenseName"
        }
    }
    $BuildInfo = [ordered]@{
        version = $Version
        commit = (git rev-parse HEAD)
        working_tree_dirty = [bool](git status --porcelain --untracked-files=no)
        python = (python --version)
        packaged_at_utc = [DateTime]::UtcNow.ToString('o')
        platform = 'windows-x64'
    }
    $BuildInfo | ConvertTo-Json | Set-Content -LiteralPath "$PackageDir/BUILD_INFO.json" -Encoding utf8
    $ZipPath = Join-Path $ProjectRoot "dist/photo-renamer-$Version-windows-x64.zip"
    Compress-Archive -Path "$PackageDir/*" -DestinationPath $ZipPath -Force
    $Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $([IO.Path]::GetFileName($ZipPath))" | Set-Content -LiteralPath dist/SHA256SUMS.txt -Encoding ascii
    Write-Output $ZipPath
} finally {
    Pop-Location
}
