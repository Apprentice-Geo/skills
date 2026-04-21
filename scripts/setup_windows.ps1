[CmdletBinding()]
param(
    [switch]$SkipDownloads,
    [switch]$SkipPythonInstall,
    [switch]$SkipModelDownload,
    [switch]$SkipVerify,
    [string]$PythonCommand = "",
    [string]$PipIndexUrl = "",
    [string]$HfEndpoint = "",
    [string]$WhisperModelRepo = "Systran/faster-whisper-small",
    [string]$WhisperModelDirName = "faster-whisper-small"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DefaultFfmpegUrl = "https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
$DefaultPipIndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"
$DefaultHfEndpoint = "https://hf-mirror.com"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Ensure-Directory {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path | Out-Null
    }
}

function Resolve-Python {
    param([string]$PreferredCommand)

    if ($PreferredCommand) {
        $cmd = Get-Command $PreferredCommand -ErrorAction Stop
        return $cmd.Source
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }

    throw "Python 3 was not found. Install Python 3 first, or pass -PythonCommand <path>."
}

function Invoke-Python {
    param(
        [string]$PythonExe,
        [string[]]$Arguments
    )

    if ((Split-Path -Leaf $PythonExe) -ieq "py.exe") {
        & $PythonExe -3 @Arguments
    }
    else {
        & $PythonExe @Arguments
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: $PythonExe $($Arguments -join ' ')"
    }
}

function Invoke-DownloadFile {
    param(
        [string]$Url,
        [string]$Destination
    )

    Ensure-Directory (Split-Path -Parent $Destination)

    Write-Host "Downloading: $Url"
    Write-Host "To: $Destination"

    $tempPath = "$Destination.download"
    if (Test-Path -LiteralPath $tempPath) {
        Remove-Item -LiteralPath $tempPath -Force
    }

    try {
        Invoke-WebRequest -Uri $Url -OutFile $tempPath -UseBasicParsing
        Move-Item -LiteralPath $tempPath -Destination $Destination -Force
    }
    catch {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force
        }
        throw
    }
}

function Expand-FfmpegArchive {
    param(
        [string]$ArchivePath,
        [string]$InstallDir
    )

    $extractDir = Join-Path $InstallDir "_ffmpeg_extract"
    if (Test-Path -LiteralPath $extractDir) {
        Remove-Item -LiteralPath $extractDir -Recurse -Force
    }
    Ensure-Directory $extractDir

    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $extractDir -Force

    $ffmpegExe = Get-ChildItem -Path $extractDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $ffmpegExe) {
        throw "ffmpeg.exe was not found in archive: $ArchivePath"
    }

    $ffmpegRoot = Split-Path -Parent (Split-Path -Parent $ffmpegExe.FullName)
    $targetDir = Join-Path $InstallDir "ffmpeg"

    if (Test-Path -LiteralPath $targetDir) {
        Remove-Item -LiteralPath $targetDir -Recurse -Force
    }

    Move-Item -LiteralPath $ffmpegRoot -Destination $targetDir
    Remove-Item -LiteralPath $extractDir -Recurse -Force
}

function Test-CommandVersion {
    param(
        [string]$ExePath,
        [string[]]$Arguments
    )

    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "Missing executable: $ExePath"
    }

    & $ExePath @Arguments | Select-Object -First 5
    if ($LASTEXITCODE -ne 0) {
        throw "Version check failed: $ExePath $($Arguments -join ' ')"
    }
}

function Find-SystemCommand {
    param([string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    return ""
}

function Resolve-PythonPackageFfmpeg {
    param([string]$PythonExe)

    $resolver = Join-Path $PSScriptRoot "resolve_ffmpeg_binaries.py"
    $result = & $PythonExe $resolver
    if ($LASTEXITCODE -ne 0 -or -not $result -or $result.Count -lt 2) {
        return @("", "")
    }

    return @([string]$result[0], [string]$result[1])
}

$SkillRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvDir = Join-Path $SkillRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$RequirementsPath = Join-Path $SkillRoot "requirements.txt"
$ToolsDir = Join-Path $SkillRoot "tools"
$BinDir = Join-Path $ToolsDir "bin"
$CacheDir = Join-Path $ToolsDir "cache"
$ModelsDir = Join-Path $ToolsDir "models"
$ResultsDir = Join-Path $SkillRoot "results"
$WhisperModelDir = Join-Path $ModelsDir $WhisperModelDirName
$FfmpegDir = Join-Path $BinDir "ffmpeg"
$FfmpegExe = Join-Path $FfmpegDir "bin\ffmpeg.exe"
$FfprobeExe = Join-Path $FfmpegDir "bin\ffprobe.exe"
$ResolvedFfmpegExe = ""
$ResolvedFfprobeExe = ""

if (-not $PipIndexUrl -and $env:PIP_INDEX_URL) {
    $PipIndexUrl = $env:PIP_INDEX_URL
}
if (-not $PipIndexUrl) {
    $PipIndexUrl = $DefaultPipIndexUrl
}

if (-not $HfEndpoint -and $env:HF_ENDPOINT) {
    $HfEndpoint = $env:HF_ENDPOINT
}
if (-not $HfEndpoint) {
    $HfEndpoint = $DefaultHfEndpoint
}
$env:HF_ENDPOINT = $HfEndpoint

Write-Host "Skill root: $SkillRoot"
Write-Host "Using HF endpoint: $env:HF_ENDPOINT"

Write-Step "Create local directories"
Ensure-Directory $BinDir
Ensure-Directory $CacheDir
Ensure-Directory $ModelsDir
Ensure-Directory $ResultsDir

Write-Step "Create Python virtual environment"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    $SystemPython = Resolve-Python $PythonCommand
    Write-Host "Using Python: $SystemPython"
    Invoke-Python $SystemPython @("-m", "venv", $VenvDir)
}
else {
    Write-Host "Virtual environment already exists: $VenvDir"
}

if (-not $SkipPythonInstall) {
    Write-Step "Install Python dependencies into .venv"
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }

    $pipArgs = @("install", "-r", $RequirementsPath)
    if ($PipIndexUrl) {
        $pipArgs = @("install", "-i", $PipIndexUrl, "-r", $RequirementsPath)
        Write-Host "Using pip index: $PipIndexUrl"
    }

    & $VenvPython -m pip @pipArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install requirements."
    }
}
else {
    Write-Host "Skipping Python dependency installation."
}

Write-Step "Resolve ffmpeg"
$systemFfmpeg = Find-SystemCommand "ffmpeg"
$systemFfprobe = Find-SystemCommand "ffprobe"

if ($systemFfmpeg -and $systemFfprobe) {
    $ResolvedFfmpegExe = $systemFfmpeg
    $ResolvedFfprobeExe = $systemFfprobe
    Write-Host "Using system ffmpeg: $ResolvedFfmpegExe"
    Write-Host "Using system ffprobe: $ResolvedFfprobeExe"
}
else {
    $packageFfmpeg = Resolve-PythonPackageFfmpeg $VenvPython
    if ($packageFfmpeg[0] -and $packageFfmpeg[1]) {
        $ResolvedFfmpegExe = $packageFfmpeg[0]
        $ResolvedFfprobeExe = $packageFfmpeg[1]
        Write-Host "Using ffmpeg-binaries-compat ffmpeg: $ResolvedFfmpegExe"
        Write-Host "Using ffmpeg-binaries-compat ffprobe: $ResolvedFfprobeExe"
    }
    elseif ((Test-Path -LiteralPath $FfmpegExe) -and (Test-Path -LiteralPath $FfprobeExe)) {
        $ResolvedFfmpegExe = $FfmpegExe
        $ResolvedFfprobeExe = $FfprobeExe
        Write-Host "Using cached portable ffmpeg: $FfmpegDir"
    }
    elseif (-not $SkipDownloads) {
        Write-Host "System ffmpeg/ffprobe and ffmpeg-binaries-compat were not found. Installing portable ffmpeg from the default GitHub release."
        $ffmpegZip = Join-Path $CacheDir "ffmpeg-win64.zip"
        Invoke-DownloadFile -Url $DefaultFfmpegUrl -Destination $ffmpegZip
        Expand-FfmpegArchive -ArchivePath $ffmpegZip -InstallDir $BinDir
        $ResolvedFfmpegExe = $FfmpegExe
        $ResolvedFfprobeExe = $FfprobeExe
    }
    else {
        Write-Warning "ffmpeg/ffprobe not found and downloads are skipped."
    }
}

if (-not $SkipModelDownload) {
    Write-Step "Install default faster-whisper model"
    if (Test-Path -LiteralPath (Join-Path $WhisperModelDir "model.bin")) {
        Write-Host "Model already exists: $WhisperModelDir"
    }
    else {
        & $VenvPython -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='$WhisperModelRepo', local_dir=r'$WhisperModelDir')"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to download faster-whisper model: $WhisperModelRepo"
        }
    }
}
else {
    Write-Host "Skipping faster-whisper model download."
}

if (-not $SkipVerify) {
    Write-Step "Verify installation"
    & $VenvPython -m yt_dlp --version
    if ($LASTEXITCODE -ne 0) {
        throw "yt-dlp check failed."
    }

    & $VenvPython -c "import faster_whisper; print('faster-whisper ok')"
    if ($LASTEXITCODE -ne 0) {
        throw "faster-whisper import check failed."
    }

    if (Test-Path -LiteralPath (Join-Path $WhisperModelDir "model.bin")) {
        Write-Host "faster-whisper model ok: $WhisperModelDir"
    }
    else {
        throw "faster-whisper model is missing: $WhisperModelDir"
    }

    if ($ResolvedFfmpegExe) {
        Test-CommandVersion -ExePath $ResolvedFfmpegExe -Arguments @("-version")
    }
    else {
        throw "ffmpeg is missing. Install it system-wide or re-run without -SkipDownloads."
    }

    if ($ResolvedFfprobeExe) {
        Test-CommandVersion -ExePath $ResolvedFfprobeExe -Arguments @("-version")
    }
    else {
        throw "ffprobe is missing. Install it system-wide or re-run without -SkipDownloads."
    }
}
else {
    Write-Host "Skipping installation verification."
}

Write-Host ""
Write-Host "Setup completed."
Write-Host "Run scripts with:"
Write-Host "  .\.venv\Scripts\python.exe scripts\run_pipeline.py ""<bilibili-url>"""
Write-Host ""