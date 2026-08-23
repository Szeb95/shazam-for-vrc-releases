[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SetupPath,
    [ValidateRange(2, 30)]
    [int]$LaunchSeconds = 5
)

$ErrorActionPreference = "Stop"

$resolvedSetupPath = (Resolve-Path -LiteralPath $SetupPath).Path
$temporaryBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$testDirectoryName = "ShazamForVRC-InstallerTest-$([guid]::NewGuid().ToString('N'))"
$testRoot = Join-Path $temporaryBase $testDirectoryName
$installDirectory = Join-Path $testRoot "App"
$installLog = Join-Path $testRoot "install.log"
$appExecutable = Join-Path $installDirectory "Shazam for VRC.exe"
$ffmpegExecutable = Join-Path $installDirectory "_internal\ffmpeg.exe"
$ffprobeExecutable = Join-Path $installDirectory "_internal\ffprobe.exe"
$appProcess = $null
$installed = $false

$uninstallRegistryRoots = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
)
$existingInstall = foreach ($registryRoot in $uninstallRegistryRoots) {
    if (-not (Test-Path -LiteralPath $registryRoot)) {
        continue
    }
    Get-ChildItem -LiteralPath $registryRoot -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction SilentlyContinue } |
        Where-Object { $_.DisplayName -like "Shazam for VRC*" }
}
if ($existingInstall) {
    throw (
        "An installed copy of Shazam for VRC already exists. The isolated test would share " +
        "its Windows app identity. Uninstall that copy first or run this test in Windows Sandbox."
    )
}

function Remove-TestDirectory {
    param([string]$Path)

    $resolvedTarget = [System.IO.Path]::GetFullPath($Path)
    $expectedPrefix = Join-Path $temporaryBase "ShazamForVRC-InstallerTest-"
    if (-not $resolvedTarget.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove an unexpected test directory: $resolvedTarget"
    }
    if (Test-Path -LiteralPath $resolvedTarget) {
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $testRoot -Force | Out-Null

try {
    Write-Host "Installing silently into the temporary test folder..."
    $installArguments = @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/NOICONS",
        "/DIR=`"$installDirectory`"",
        "/LOG=`"$installLog`""
    )
    $installerProcess = Start-Process `
        -FilePath $resolvedSetupPath `
        -ArgumentList $installArguments `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($installerProcess.ExitCode -ne 0) {
        throw "The installer exited with code $($installerProcess.ExitCode). See $installLog"
    }
    $installed = $true

    if (-not (Test-Path -LiteralPath $appExecutable -PathType Leaf)) {
        throw "The installer did not create the application executable."
    }
    if (-not (Test-Path -LiteralPath $ffmpegExecutable -PathType Leaf)) {
        throw "The installer did not include FFmpeg."
    }
    if (-not (Test-Path -LiteralPath $ffprobeExecutable -PathType Leaf)) {
        throw "The installer did not include FFprobe."
    }

    Write-Host "Checking the bundled FFmpeg executable..."
    $ffmpegProcess = Start-Process `
        -FilePath $ffmpegExecutable `
        -ArgumentList @("-version") `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($ffmpegProcess.ExitCode -ne 0) {
        throw "Bundled FFmpeg exited with code $($ffmpegProcess.ExitCode)."
    }

    Write-Host "Checking the bundled FFprobe executable..."
    $ffprobeProcess = Start-Process `
        -FilePath $ffprobeExecutable `
        -ArgumentList @("-version") `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($ffprobeProcess.ExitCode -ne 0) {
        throw "Bundled FFprobe exited with code $($ffprobeProcess.ExitCode)."
    }

    Write-Host "Checking ShazamIO's packaged audio-decoder path..."
    $selfTestProcess = Start-Process `
        -FilePath $appExecutable `
        -ArgumentList @("--self-test") `
        -WorkingDirectory $installDirectory `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($selfTestProcess.ExitCode -ne 0) {
        throw "The packaged recognition runtime self-test exited with code $($selfTestProcess.ExitCode)."
    }

    Write-Host "Starting the installed app for $LaunchSeconds seconds..."
    $appProcess = Start-Process `
        -FilePath $appExecutable `
        -WorkingDirectory $installDirectory `
        -PassThru `
        -WindowStyle Hidden
    Start-Sleep -Seconds $LaunchSeconds
    if ($appProcess.HasExited) {
        throw "The installed app exited unexpectedly with code $($appProcess.ExitCode)."
    }
    Stop-Process -Id $appProcess.Id
    Wait-Process -Id $appProcess.Id -ErrorAction SilentlyContinue
    $appProcess = $null

    $uninstaller = Get-ChildItem `
        -LiteralPath $installDirectory `
        -Filter "unins*.exe" `
        -File | Select-Object -First 1
    if ($null -eq $uninstaller) {
        throw "The uninstall executable was not created in the installed app folder."
    }

    Write-Host "Running the uninstaller..."
    $uninstallProcess = Start-Process `
        -FilePath $uninstaller.FullName `
        -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($uninstallProcess.ExitCode -ne 0) {
        throw "The uninstaller exited with code $($uninstallProcess.ExitCode)."
    }
    $installed = $false
    if (Test-Path -LiteralPath $appExecutable) {
        throw "The application executable remained after uninstalling."
    }

    Write-Host "Installer test passed: install, bundled media tools, launch, and uninstall all worked."
} finally {
    if ($null -ne $appProcess -and -not $appProcess.HasExited) {
        Stop-Process -Id $appProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($installed -and (Test-Path -LiteralPath $installDirectory)) {
        $remainingUninstaller = Get-ChildItem `
            -LiteralPath $installDirectory `
            -Filter "unins*.exe" `
            -File `
            -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $remainingUninstaller) {
            Start-Process `
                -FilePath $remainingUninstaller.FullName `
                -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") `
                -Wait `
                -WindowStyle Hidden `
                -ErrorAction SilentlyContinue | Out-Null
        }
    }
    Remove-TestDirectory -Path $testRoot
}
