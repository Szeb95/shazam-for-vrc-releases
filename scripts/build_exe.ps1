[CmdletBinding()]
param(
    [string]$FFmpegPath,
    [string]$FFprobePath,
    [string]$FFmpegLicensePath
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pyprojectPath = Join-Path $projectRoot "pyproject.toml"
$pythonBuildBootstrap = Join-Path $projectRoot "scripts\python_build_bootstrap"

function Remove-GeneratedDirectory {
    param([string]$RelativePath)

    $allowedRelativePaths = @(
        "build\Shazam for VRC",
        "dist\Shazam for VRC"
    )
    if ($RelativePath -notin $allowedRelativePaths) {
        throw "Refusing to clear an unexpected build path: $RelativePath"
    }

    $target = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $RelativePath))
    $workspace = [System.IO.Path]::GetFullPath($projectRoot)
    if (-not $target.StartsWith(
        ($workspace + [System.IO.Path]::DirectorySeparatorChar),
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to clear a generated directory outside the workspace: $target"
    }
    if (-not (Test-Path -LiteralPath $target)) {
        return
    }

    Get-ChildItem -LiteralPath $target -Force -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object {
            $_.Attributes = $_.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
        }
    $targetItem = Get-Item -LiteralPath $target -Force
    $targetItem.Attributes = $targetItem.Attributes -band (
        -bnot [System.IO.FileAttributes]::ReadOnly
    )
    Remove-Item -LiteralPath $target -Recurse -Force
}

function Resolve-FFmpegExecutable {
    param([string]$RequestedPath)

    $candidates = @()
    if ($RequestedPath) {
        $candidates += $RequestedPath
    }
    if ($env:FFMPEG_PATH) {
        $candidates += $env:FFMPEG_PATH
    }
    $candidates += Join-Path $projectRoot "tools\ffmpeg\bin\ffmpeg.exe"

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $command = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($null -ne $command -and $command.Source) {
        return $command.Source
    }

    throw (
        "FFmpeg was not found. Install it for the build machine, set FFMPEG_PATH, " +
        "or pass -FFmpegPath. FFmpeg is mandatory because it is bundled for end users."
    )
}

function Resolve-FFmpegLicense {
    param(
        [string]$RequestedPath,
        [string]$ExecutablePath
    )

    if ($RequestedPath) {
        if (-not (Test-Path -LiteralPath $RequestedPath -PathType Leaf)) {
            throw "The FFmpeg license file was not found: $RequestedPath"
        }
        return (Resolve-Path -LiteralPath $RequestedPath).Path
    }

    $directory = Split-Path -Parent $ExecutablePath
    $candidateNames = @("LICENSE", "LICENSE.txt", "COPYING.GPLv3", "COPYING.LGPLv2.1", "COPYING")
    for ($level = 0; $level -lt 3 -and $directory; $level += 1) {
        foreach ($name in $candidateNames) {
            $candidate = Join-Path $directory $name
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return (Resolve-Path -LiteralPath $candidate).Path
            }
        }
        $directory = Split-Path -Parent $directory
    }

    throw (
        "The FFmpeg license file was not found near $ExecutablePath. " +
        "Pass its path with -FFmpegLicensePath so the redistributable build includes it."
    )
}

function Resolve-FFprobeExecutable {
    param(
        [string]$RequestedPath,
        [string]$FFmpegExecutablePath
    )

    $candidates = @()
    if ($RequestedPath) {
        $candidates += $RequestedPath
    }
    if ($env:FFPROBE_PATH) {
        $candidates += $env:FFPROBE_PATH
    }
    $candidates += Join-Path (Split-Path -Parent $FFmpegExecutablePath) "ffprobe.exe"
    $candidates += Join-Path $projectRoot "tools\ffmpeg\bin\ffprobe.exe"

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $command = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($null -ne $command -and $command.Source) {
        return $command.Source
    }

    throw (
        "FFprobe was not found. Install it beside FFmpeg, set FFPROBE_PATH, or pass " +
        "-FFprobePath. Shazam's alternate fingerprint method requires bundled FFprobe."
    )
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Create the project virtual environment and install .[dev] before building."
}

Write-Host "Checking the Python GUI runtime..."
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = if ($previousPythonPath) {
        "$pythonBuildBootstrap;$previousPythonPath"
    } else {
        $pythonBuildBootstrap
    }
    & $pythonPath -c (
        "import tkinter; " +
        "tcl = tkinter.Tcl(); " +
        "assert tcl.eval('info patchlevel'), 'Tcl did not initialize'"
    )
    if ($LASTEXITCODE -ne 0) {
        throw (
            "The build Python does not have a working Tkinter/Tcl/Tk runtime. " +
            "Use a complete Python 3.12 installation before packaging the GUI."
        )
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}

$pyprojectText = Get-Content -LiteralPath $pyprojectPath -Raw
$versionMatch = [regex]::Match($pyprojectText, '(?m)^version\s*=\s*"(?<version>\d+\.\d+\.\d+)"')
if (-not $versionMatch.Success) {
    throw "Could not read the application version from pyproject.toml."
}
$appVersion = $versionMatch.Groups["version"].Value
$versionParts = $appVersion.Split(".")
$fileVersion = "$($versionParts[0]), $($versionParts[1]), $($versionParts[2]), 0"

$ffmpegExecutable = Resolve-FFmpegExecutable -RequestedPath $FFmpegPath
$ffprobeExecutable = Resolve-FFprobeExecutable `
    -RequestedPath $FFprobePath `
    -FFmpegExecutablePath $ffmpegExecutable
$ffmpegLicense = Resolve-FFmpegLicense -RequestedPath $FFmpegLicensePath -ExecutablePath $ffmpegExecutable

$versionFileDirectory = Join-Path $projectRoot "build\packaging"
New-Item -ItemType Directory -Path $versionFileDirectory -Force | Out-Null
$versionFile = Join-Path $versionFileDirectory "windows-version-info.txt"
$versionInfo = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($fileVersion),
    prodvers=($fileVersion),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'Szeb'),
         StringStruct(u'FileDescription', u'Shazam for VRC'),
         StringStruct(u'FileVersion', u'$appVersion'),
         StringStruct(u'InternalName', u'Shazam for VRC'),
         StringStruct(u'OriginalFilename', u'Shazam for VRC.exe'),
         StringStruct(u'ProductName', u'Shazam for VRC'),
         StringStruct(u'ProductVersion', u'$appVersion')]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"@
[System.IO.File]::WriteAllText(
    $versionFile,
    $versionInfo,
    [System.Text.UTF8Encoding]::new($false)
)

Remove-GeneratedDirectory -RelativePath "build\Shazam for VRC"
Remove-GeneratedDirectory -RelativePath "dist\Shazam for VRC"

$arguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--onedir",
    "--name", "Shazam for VRC",
    "--version-file", $versionFile,
    "--paths", (Join-Path $projectRoot "src"),
    "--collect-all", "openvr",
    "--collect-all", "pyaudiowpatch",
    "--collect-all", "shazamio",
    "--collect-all", "yt_dlp",
    "--add-data",
    "$(Join-Path $projectRoot 'src\shazam_for_vrc\input\steamvr_actions');shazam_for_vrc\input\steamvr_actions",
    "--add-binary", "$ffmpegExecutable;.",
    "--add-binary", "$ffprobeExecutable;.",
    "--add-data", "$ffmpegLicense;licenses\ffmpeg"
)
$arguments += Join-Path $projectRoot "src\shazam_for_vrc\main.py"

Write-Host "Building Shazam for VRC $appVersion with bundled Python, FFmpeg, and FFprobe..."
Push-Location $projectRoot
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = if ($previousPythonPath) {
        "$pythonBuildBootstrap;$previousPythonPath"
    } else {
        $pythonBuildBootstrap
    }
    & $pythonPath @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "The executable build failed with exit code $LASTEXITCODE."
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}

$outputExecutable = Join-Path $projectRoot "dist\Shazam for VRC\Shazam for VRC.exe"
$bundledFFmpeg = Join-Path $projectRoot "dist\Shazam for VRC\_internal\ffmpeg.exe"
$bundledFFprobe = Join-Path $projectRoot "dist\Shazam for VRC\_internal\ffprobe.exe"
if (-not (Test-Path -LiteralPath $outputExecutable -PathType Leaf)) {
    throw "The executable build finished without producing $outputExecutable"
}
if (-not (Test-Path -LiteralPath $bundledFFmpeg -PathType Leaf)) {
    throw "The executable build finished without bundling FFmpeg at $bundledFFmpeg"
}
if (-not (Test-Path -LiteralPath $bundledFFprobe -PathType Leaf)) {
    throw "The executable build finished without bundling FFprobe at $bundledFFprobe"
}

Write-Host "Built standalone application: $outputExecutable"
Write-Host "End users do not need to install Python, FFmpeg, or FFprobe."
