[CmdletBinding()]
param(
    [string]$FFmpegPath,
    [string]$FFprobePath,
    [string]$FFmpegLicensePath,
    [string]$InnoCompilerPath,
    [string]$UpdateSigningKeyPath
)

$ErrorActionPreference = "Stop"

$buildExecutableScript = Join-Path $PSScriptRoot "build_exe.ps1"
$buildInstallerScript = Join-Path $PSScriptRoot "build_installer.ps1"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$buildAvatarPackageScript = Join-Path $PSScriptRoot "build_avatar_package.py"

& $buildExecutableScript `
    -FFmpegPath $FFmpegPath `
    -FFprobePath $FFprobePath `
    -FFmpegLicensePath $FFmpegLicensePath

& $buildInstallerScript `
    -InnoCompilerPath $InnoCompilerPath `
    -UpdateSigningKeyPath $UpdateSigningKeyPath

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "The project virtual environment was not found at $pythonPath."
}

& $pythonPath $buildAvatarPackageScript `
    --output-directory (Join-Path $projectRoot "dist\avatar")
if ($LASTEXITCODE -ne 0) {
    throw "Building the avatar package failed with exit code $LASTEXITCODE."
}
