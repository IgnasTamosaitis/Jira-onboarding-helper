[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$SkipDependencyInstall,
    [string]$SigningCertificateThumbprint = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$versionFile = Join-Path $repoRoot "version.txt"

if (-not $Version) {
    $Version = (Get-Content -LiteralPath $versionFile -Raw).Trim()
}
$Version = $Version.TrimStart("v")
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must use major.minor.patch format. Received '$Version'."
}

$buildRoot = Join-Path $repoRoot "build"
$distRoot = Join-Path $repoRoot "dist"
$appDist = Join-Path $distRoot "app"
$installerDist = Join-Path $distRoot "installer"
$iconFile = Join-Path $buildRoot "JiraReminders.ico"
$versionInfo = Join-Path $buildRoot "JiraReminders.version.txt"
$appExe = Join-Path $appDist "JiraReminders.exe"
$wixProject = Join-Path $PSScriptRoot "installer\JiraReminders.wixproj"
$wixBin = Join-Path $PSScriptRoot "installer\bin"
$wixObj = Join-Path $PSScriptRoot "installer\obj"

function Invoke-CodeSign([string]$Path) {
    if (-not $SigningCertificateThumbprint) {
        return
    }
    $signTool = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if (-not $signTool) {
        throw "signtool.exe was not found. Install the Windows SDK before requesting code signing."
    }
    & $signTool.Source sign /sha1 $SigningCertificateThumbprint /fd SHA256 `
        /tr $TimestampUrl /td SHA256 $Path
    if ($LASTEXITCODE -ne 0) {
        throw "Code signing failed for $Path."
    }
}

if (-not $SkipDependencyInstall) {
    & python -m pip install --disable-pip-version-check -r (Join-Path $repoRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Installing application dependencies failed." }
    & python -m pip install --disable-pip-version-check -r (Join-Path $repoRoot "requirements-build.txt")
    if ($LASTEXITCODE -ne 0) { throw "Installing build dependencies failed." }
}

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw ".NET SDK 8 or later is required to build the MSI. Install it, then run this script again."
}

New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
foreach ($path in @($buildRoot, $appDist, $installerDist)) {
    $resolvedParent = (Resolve-Path (Split-Path $path -Parent)).Path
    if (-not $resolvedParent.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the repository: $path"
    }
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
foreach ($path in @($wixBin, $wixObj)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}

& python (Join-Path $PSScriptRoot "create_icon.py") $iconFile
if ($LASTEXITCODE -ne 0) { throw "Icon generation failed." }
& python (Join-Path $PSScriptRoot "create_version_info.py") $Version $versionInfo
if ($LASTEXITCODE -ne 0) { throw "Version-resource generation failed." }

$pyInstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "JiraReminders",
    "--icon", $iconFile,
    "--version-file", $versionInfo,
    "--distpath", $appDist,
    "--workpath", (Join-Path $buildRoot "pyinstaller"),
    "--specpath", $buildRoot,
    "--hidden-import", "keyring.backends.Windows",
    "--hidden-import", "plyer.platforms.win.notification",
    "--hidden-import", "pystray._win32",
    (Join-Path $repoRoot "app.py")
)
& python @pyInstallerArgs
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $appExe)) {
    throw "Building JiraReminders.exe failed."
}
Invoke-CodeSign $appExe

$env:ProductVersion = $Version
$env:AppSource = $appExe
$env:VersionFile = $versionFile
$env:IconSource = $iconFile
& dotnet build $wixProject --configuration Release
if ($LASTEXITCODE -ne 0) { throw "Building the MSI failed." }

$msi = Join-Path $installerDist "Jira-Reminders-$Version.msi"
$builtMsi = Join-Path $wixBin "Release\Jira-Reminders-$Version.msi"
if (Test-Path -LiteralPath $builtMsi) {
    Copy-Item -LiteralPath $builtMsi -Destination $msi -Force
}
if (-not (Test-Path -LiteralPath $msi)) {
    throw "MSI build completed but the expected file was not found: $msi"
}
Invoke-CodeSign $msi

Write-Host ""
Write-Host "Installer ready:" -ForegroundColor Green
Write-Host $msi
