[CmdletBinding()]
param(
    [switch]$InstallQwen3,
    [switch]$DownloadQwen3Models,
    [string]$PythonCommand = "",
    [string]$PipIndexUrl = "",
    [string]$HfEndpoint = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DefaultPipIndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"
$DefaultHfEndpoint = "https://hf-mirror.com"
$WhisperModelRepo = "Systran/faster-whisper-small"
$Qwen3AsrModelRepo = "Qwen/Qwen3-ASR-0.6B"
$Qwen3AlignerModelRepo = "Qwen/Qwen3-ForcedAligner-0.6B"
$Qwen3TorchIndexUrl = "https://download.pytorch.org/whl/cu126"
$RecommendedPythonVersion = "3.12"
$MinimumPythonVersion = [version]"3.12.0"

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

function Get-PythonVersion {
    param([string]$PythonExe)

    if (-not (Test-Path -LiteralPath $PythonExe) -and -not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
        return $null
    }

    if ((Split-Path -Leaf $PythonExe) -ieq "py.exe") {
        $versionText = & $PythonExe -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    }
    else {
        $versionText = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    }

    if ($LASTEXITCODE -ne 0 -or -not $versionText) {
        return $null
    }

    return [version]([string]@($versionText)[0])
}

function Assert-CompatiblePython {
    param(
        [string]$PythonExe,
        [string]$Context
    )

    $version = Get-PythonVersion $PythonExe
    if (-not $version) {
        throw "Unable to determine Python version for ${Context}: $PythonExe"
    }

    if ($version -lt $MinimumPythonVersion) {
        throw "${Context} uses Python $version, but this skill requires Python >= $MinimumPythonVersion."
    }

    Write-Host "${Context} Python version: $version"
}

function Find-Uv {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        return $uv.Source
    }

    return ""
}

function Resolve-CompatibleSystemPython {
    param([string]$PreferredCommand)

    $candidateCommands = @()
    if ($PreferredCommand) {
        $candidateCommands += $PreferredCommand
    }
    else {
        $candidateCommands += "python"
        $candidateCommands += "py"
    }

    foreach ($candidateCommand in $candidateCommands) {
        $command = Get-Command $candidateCommand -ErrorAction SilentlyContinue
        if (-not $command) {
            continue
        }

        $version = Get-PythonVersion $command.Source
        if ($version -and $version -ge $MinimumPythonVersion) {
            Write-Host "Using system Python: $($command.Source)"
            Write-Host "System Python version: $version"
            return $command.Source
        }

        if ($version) {
            Write-Host "Skipping Python $version at $($command.Source); requires >= $MinimumPythonVersion."
        }
    }

    if ($PreferredCommand) {
        throw "The specified -PythonCommand is missing or uses Python < $MinimumPythonVersion."
    }

    throw @"
No compatible Python runtime was found.

This skill requires Python >= $MinimumPythonVersion.
Recommended: install uv, then rerun setup.

uv official site:
  https://docs.astral.sh/uv/

After installing uv:
  .\scripts\setup_windows.ps1
"@
}

function New-SkillVirtualEnvironment {
    param(
        [string]$VenvDir,
        [string]$VenvPython,
        [string]$PreferredPythonCommand
    )

    if (Test-Path -LiteralPath $VenvPython) {
        Write-Host "Virtual environment already exists: $VenvDir"
        Assert-CompatiblePython -PythonExe $VenvPython -Context "Existing .venv"
        return
    }

    if ($PreferredPythonCommand) {
        $python = Resolve-CompatibleSystemPython $PreferredPythonCommand
        Invoke-Python $python @("-m", "venv", $VenvDir)
        Assert-CompatiblePython -PythonExe $VenvPython -Context "Created .venv"
        return
    }

    $uv = Find-Uv
    if ($uv) {
        Write-Host "Using uv: $uv"
        Write-Host "Preparing Python $RecommendedPythonVersion with uv"
        & $uv python install $RecommendedPythonVersion
        if ($LASTEXITCODE -ne 0) {
            throw "uv failed to install Python $RecommendedPythonVersion."
        }

        Write-Host "Creating .venv with Python $RecommendedPythonVersion"
        & $uv venv --seed --python $RecommendedPythonVersion $VenvDir
        if ($LASTEXITCODE -ne 0) {
            throw "uv failed to create virtual environment: $VenvDir"
        }

        Assert-CompatiblePython -PythonExe $VenvPython -Context "Created .venv"
        return
    }

    Write-Host "uv was not found; falling back to system Python >= $MinimumPythonVersion."
    $systemPython = Resolve-CompatibleSystemPython ""
    Invoke-Python $systemPython @("-m", "venv", $VenvDir)
    Assert-CompatiblePython -PythonExe $VenvPython -Context "Created .venv"
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

function Test-ModelWeights {
    param(
        [string]$ModelDir,
        [string[]]$WeightFiles
    )

    foreach ($WeightFile in $WeightFiles) {
        if (Test-Path -LiteralPath (Join-Path $ModelDir $WeightFile)) {
            return $true
        }
    }

    return $false
}

$SkillRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvDir = Join-Path $SkillRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$RequirementsPath = Join-Path $SkillRoot "requirements.txt"
$Qwen3RequirementsPath = Join-Path $SkillRoot "requirements-qwen3.txt"
$ToolsDir = Join-Path $SkillRoot "tools"
$ModelsDir = Join-Path $ToolsDir "models"
$ResultsDir = Join-Path $SkillRoot "results"
$WhisperModelDir = Join-Path $ModelsDir "faster-whisper-small"
$Qwen3AsrModelDir = Join-Path $ModelsDir "qwen3-asr-0.6b"
$Qwen3AlignerModelDir = Join-Path $ModelsDir "qwen3-forcedaligner-0.6b"
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
Ensure-Directory $ModelsDir
Ensure-Directory $ResultsDir

Write-Step "Create Python virtual environment"
New-SkillVirtualEnvironment -VenvDir $VenvDir -VenvPython $VenvPython -PreferredPythonCommand $PythonCommand

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

if ($InstallQwen3) {
    $torchArgs = @("install", "--index-url", $Qwen3TorchIndexUrl, "torch", "torchaudio")
    Write-Host "Installing torch and torchaudio from PyTorch CUDA index: $Qwen3TorchIndexUrl"
    & $VenvPython -m pip @torchArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install CUDA torch/torchaudio from: $Qwen3TorchIndexUrl"
    }

    if (-not (Test-Path -LiteralPath $Qwen3RequirementsPath)) {
        throw "Qwen3 requirements file not found: $Qwen3RequirementsPath"
    }

    $qwenPipArgs = @("install", "-r", $Qwen3RequirementsPath)
    if ($PipIndexUrl) {
        $qwenPipArgs = @("install", "-i", $PipIndexUrl, "-r", $Qwen3RequirementsPath)
    }

    & $VenvPython -m pip @qwenPipArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Qwen3 requirements."
    }
}

if ($DownloadQwen3Models -and -not $InstallQwen3) {
    $hubArgs = @("install", "huggingface_hub")
    if ($PipIndexUrl) {
        $hubArgs = @("install", "-i", $PipIndexUrl, "huggingface_hub")
    }

    & $VenvPython -m pip @hubArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install huggingface_hub for Qwen3 model download."
    }
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
    else {
        Write-Warning "ffmpeg/ffprobe not found. Install them system-wide or ensure ffmpeg-binaries-compat is available in .venv."
    }
}

Write-Step "Install default faster-whisper model"
if (Test-ModelWeights $WhisperModelDir @("model.bin")) {
    Write-Host "Model already exists: $WhisperModelDir"
}
else {
    & $VenvPython -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='$WhisperModelRepo', local_dir=r'$WhisperModelDir')"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to download faster-whisper model: $WhisperModelRepo"
    }
}

if ($DownloadQwen3Models) {
    Write-Step "Install optional Qwen3 ASR models"

    if (Test-ModelWeights $Qwen3AsrModelDir @("model.safetensors")) {
        Write-Host "Qwen3 ASR model already exists: $Qwen3AsrModelDir"
    }
    else {
        & $VenvPython -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='$Qwen3AsrModelRepo', local_dir=r'$Qwen3AsrModelDir')"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to download Qwen3 ASR model: $Qwen3AsrModelRepo"
        }
    }

    if (Test-ModelWeights $Qwen3AlignerModelDir @("model.safetensors")) {
        Write-Host "Qwen3 aligner model already exists: $Qwen3AlignerModelDir"
    }
    else {
        & $VenvPython -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='$Qwen3AlignerModelRepo', local_dir=r'$Qwen3AlignerModelDir')"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to download Qwen3 aligner model: $Qwen3AlignerModelRepo"
        }
    }
}

Write-Step "Verify installation"
& $VenvPython -m yt_dlp --version
if ($LASTEXITCODE -ne 0) {
    throw "yt-dlp check failed."
}

& $VenvPython -c "import faster_whisper; print('faster-whisper ok')"
if ($LASTEXITCODE -ne 0) {
    throw "faster-whisper import check failed."
}

if ($InstallQwen3) {
    & $VenvPython -c "import qwen_asr; import torch; print('qwen3-asr ok'); print(torch.__version__); print(torch.cuda.is_available())"
    if ($LASTEXITCODE -ne 0) {
        throw "Qwen3 import check failed."
    }
}

if (Test-ModelWeights $WhisperModelDir @("model.bin")) {
    Write-Host "faster-whisper model ok: $WhisperModelDir"
}
else {
    throw "faster-whisper model is missing: $WhisperModelDir"
}

if ($DownloadQwen3Models) {
    if (Test-ModelWeights $Qwen3AsrModelDir @("model.safetensors")) {
        Write-Host "Qwen3 ASR model ok: $Qwen3AsrModelDir"
    }
    else {
        throw "Qwen3 ASR model is missing: $Qwen3AsrModelDir"
    }

    if (Test-ModelWeights $Qwen3AlignerModelDir @("model.safetensors")) {
        Write-Host "Qwen3 aligner model ok: $Qwen3AlignerModelDir"
    }
    else {
        throw "Qwen3 aligner model is missing: $Qwen3AlignerModelDir"
    }
}

if ($ResolvedFfmpegExe) {
    Test-CommandVersion -ExePath $ResolvedFfmpegExe -Arguments @("-version")
}
else {
    throw "ffmpeg is missing. Install it system-wide or ensure ffmpeg-binaries-compat is available in .venv."
}

if ($ResolvedFfprobeExe) {
    Test-CommandVersion -ExePath $ResolvedFfprobeExe -Arguments @("-version")
}
else {
    throw "ffprobe is missing. Install it system-wide or ensure ffmpeg-binaries-compat is available in .venv."
}

Write-Host ""
Write-Host "Setup completed."
Write-Host "Run scripts with:"
Write-Host "  .\.venv\Scripts\python.exe scripts\run_pipeline.py ""<bilibili-url>"""
Write-Host ""
