[CmdletBinding()]
param(
    [string]$InnoCompilerPath,
    [string]$UpdateSigningKeyPath
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pyprojectPath = Join-Path $projectRoot "pyproject.toml"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$appDirectory = Join-Path $projectRoot "dist\Shazam for VRC"
$appExecutable = Join-Path $appDirectory "Shazam for VRC.exe"
$bundledFFmpeg = Join-Path $appDirectory "_internal\ffmpeg.exe"
$bundledFFprobe = Join-Path $appDirectory "_internal\ffprobe.exe"
$applicationIcon = Join-Path $projectRoot "Website\favicon.ico"
$installerScript = Join-Path $projectRoot "packaging\windows\ShazamForVRC.iss"
$outputDirectory = Join-Path $projectRoot "dist\installer"
$updateManifestScript = Join-Path $projectRoot "scripts\create_update_manifest.py"
$releaseNotesScript = Join-Path $projectRoot "scripts\export_release_notes.py"

if (-not $UpdateSigningKeyPath) {
    $UpdateSigningKeyPath = Join-Path `
        ([Environment]::GetFolderPath("LocalApplicationData")) `
        "Shazam for VRC Release Signing\update-private-key.pem"
}

function Resolve-InnoCompiler {
    param([string]$RequestedPath)

    $candidates = @()
    if ($RequestedPath) {
        $candidates += $RequestedPath
    }
    if ($env:INNO_SETUP_COMPILER) {
        $candidates += $env:INNO_SETUP_COMPILER
    }

    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($null -ne $command -and $command.Source) {
        $candidates += $command.Source
    }

    $candidates += @(
        (Join-Path $projectRoot ".build-tools\Inno Setup 7\ISCC.exe"),
        (Join-Path $projectRoot ".build-tools\Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles} "Inno Setup 7\ISCC.exe"),
        (Join-Path ${env:ProgramFiles} "Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw (
        "Inno Setup was not found. Install Inno Setup 6 or 7 from " +
        "https://jrsoftware.org/isdl.php, set INNO_SETUP_COMPILER, or pass -InnoCompilerPath."
    )
}

if (-not (Test-Path -LiteralPath $appExecutable -PathType Leaf)) {
    throw "Build the standalone application first with .\scripts\build_exe.ps1."
}
if (-not (Test-Path -LiteralPath $bundledFFmpeg -PathType Leaf)) {
    throw "The standalone application is incomplete because bundled FFmpeg is missing."
}
if (-not (Test-Path -LiteralPath $bundledFFprobe -PathType Leaf)) {
    throw "The standalone application is incomplete because bundled FFprobe is missing."
}
if (-not (Test-Path -LiteralPath $applicationIcon -PathType Leaf)) {
    throw "The application icon was not found: $applicationIcon"
}
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "The project Python environment is missing. Create .venv before building."
}
if (-not (Test-Path -LiteralPath $UpdateSigningKeyPath -PathType Leaf)) {
    throw (
        "The private update signing key was not found at $UpdateSigningKeyPath. " +
        "Never put this key inside the repository."
    )
}

$pyprojectText = Get-Content -LiteralPath $pyprojectPath -Raw
$versionMatch = [regex]::Match($pyprojectText, '(?m)^version\s*=\s*"(?<version>\d+\.\d+\.\d+)"')
if (-not $versionMatch.Success) {
    throw "Could not read the application version from pyproject.toml."
}
$appVersion = $versionMatch.Groups["version"].Value
$compiler = Resolve-InnoCompiler -RequestedPath $InnoCompilerPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$arguments = @(
    "/DMyAppVersion=$appVersion",
    "/DMyAppSourceDir=$appDirectory",
    "/DMyOutputDir=$outputDirectory",
    "/DMyAppIcon=$applicationIcon",
    $installerScript
)

Write-Host "Building the Shazam for VRC $appVersion installer..."
& $compiler @arguments
if ($LASTEXITCODE -ne 0) {
    throw "The installer build failed with exit code $LASTEXITCODE."
}

$installerPath = Join-Path $outputDirectory "Shazam-for-VRC-Setup-$appVersion.exe"
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "The installer compiler finished without producing $installerPath"
}

$hash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$hashPath = "$installerPath.sha256.txt"
[System.IO.File]::WriteAllText(
    $hashPath,
    "$hash  $(Split-Path -Leaf $installerPath)`r`n",
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Built installer: $installerPath"
Write-Host "SHA-256: $hash"
Write-Host "Checksum file: $hashPath"

& $pythonPath $updateManifestScript `
    --installer $installerPath `
    --version $appVersion `
    --private-key $UpdateSigningKeyPath `
    --output-directory $outputDirectory
if ($LASTEXITCODE -ne 0) {
    throw "Creating the signed update manifest failed with exit code $LASTEXITCODE."
}

& $pythonPath $releaseNotesScript `
    --version $appVersion `
    --output-directory $outputDirectory
if ($LASTEXITCODE -ne 0) {
    throw "Exporting GitHub release notes failed with exit code $LASTEXITCODE."
}
