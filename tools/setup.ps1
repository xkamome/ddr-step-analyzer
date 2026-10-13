# DDR ステップ解析 — セットアップ（初回だけ実行）
#   1. Python 3.11〜3.13 を探す。なければ Python 3.12 をユーザー権限でインストール（管理者権限不要）
#   2. 専用の仮想環境を %LOCALAPPDATA%\ddr-pose\venv に作ってパッケージをインストール
#      （Windows のパス長制限と OneDrive 同期を避けるため、プロジェクトフォルダの外に置く）
#   3. 骨格認識モデル（約 360 MB）をダウンロード
#   4. デスクトップにショートカットを作成
# オプション: -NoShortcut（ショートカットを作らない）
# 画面のメッセージは日本語＋英語 / Messages are shown in Japanese and English
param([switch]$NoShortcut)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Say($m)  { Write-Host $m -ForegroundColor Cyan }
function Ok($m)   { Write-Host $m -ForegroundColor Green }
function Fail($m) {
  Write-Host ''
  Write-Host $m -ForegroundColor Red
  Write-Host 'うまくいかない場合は、この画面の写真を送ってください。' -ForegroundColor Yellow
  Write-Host 'If it does not work, please send a photo of this screen.' -ForegroundColor Yellow
  exit 1
}

Say '=== DDR ステップ解析：セットアップ / DDR Step Analyzer: Setup ==='
Say "フォルダ / Folder: $Root"
if ($Root -match 'OneDrive') {
  Write-Host '注意：OneDrive のフォルダ内にあります。同期でとても遅くなることがあるので、C:\ddr-step-analyzer などに移動してからの実行をおすすめします。' -ForegroundColor Yellow
  Write-Host 'Note: this folder is inside OneDrive. Syncing can make it very slow. We recommend moving it to a place like C:\ddr-step-analyzer first.' -ForegroundColor Yellow
}

function Get-PyExe([string]$cmd, [string[]]$pre) {
  try {
    $out = & $cmd @pre -c "import sys; v=sys.version_info; print(sys.executable if (v[0]==3 and 11<=v[1]<=13 and sys.maxsize>2**32) else '')" 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) {
      $e = ($out | Select-Object -Last 1).Trim()
      if ($e) { return $e }
    }
  } catch {}
  return $null
}

function Find-Python {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in '3.12', '3.13', '3.11') {
      $e = Get-PyExe 'py' @("-$v"); if ($e) { return $e }
    }
  }
  foreach ($v in '312', '313', '311') {
    $p = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe"
    if (Test-Path $p) { $e = Get-PyExe $p @(); if ($e) { return $e } }
  }
  if (Get-Command python -ErrorAction SilentlyContinue) {
    $e = Get-PyExe 'python' @(); if ($e) { return $e }
  }
  return $null
}

# ── 1. Python ──
$py = Find-Python
if (-not $py) {
  Say 'Python が見つからないので、Python 3.12 をインストールします（数分かかります）…'
  Say 'Python was not found, so Python 3.12 will be installed (this takes a few minutes)...'
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    & winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Host
    $py = Find-Python
  }
  if (-not $py) {
    $url = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
    $inst = Join-Path $env:TEMP 'python-3.12.10-amd64.exe'
    Say 'Python のインストーラーをダウンロード中… / Downloading the Python installer...'
    try {
      [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
      Invoke-WebRequest -Uri $url -OutFile $inst -UseBasicParsing
    } catch { Fail "Python のダウンロードに失敗しました。インターネット接続を確認して、もう一度 setup.bat を実行してください。`nCould not download Python. Check your internet connection and run setup.bat again.`n$_" }
    Say 'Python をインストール中… / Installing Python...'
    Start-Process -FilePath $inst -Wait -ArgumentList @('/quiet', 'InstallAllUsers=0', 'PrependPath=1',
      'Include_launcher=1', 'InstallLauncherAllUsers=0', 'Include_test=0')
    $py = Find-Python
  }
  if (-not $py) { Fail "Python のインストールに失敗しました。https://www.python.org/downloads/ から Python 3.12 を手動でインストールしてから、もう一度 setup.bat を実行してください。`nCould not install Python. Please install Python 3.12 yourself from https://www.python.org/downloads/ and run setup.bat again." }
}
Ok "Python：$py"

# ── 2. 仮想環境とパッケージ ──
$Venv = Join-Path $env:LOCALAPPDATA 'ddr-pose\venv'
$venvPy = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
  Say "専用の Python 環境を作成中… / Creating a Python environment... ($Venv)"
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Venv) | Out-Null
  & $py -m venv $Venv
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "仮想環境の作成に失敗しました。`nCould not create the Python environment." }
}
Say 'パッケージをインストール中（初回は 5〜10 分ほどかかります）…'
Say 'Installing packages (the first time takes about 5-10 minutes)...'
& $venvPy -m pip install --upgrade pip --disable-pip-version-check -q
& $venvPy -m pip install -r (Join-Path $Root 'requirements.txt') --disable-pip-version-check --no-warn-script-location
if ($LASTEXITCODE -ne 0) { Fail "パッケージのインストールに失敗しました。インターネット接続を確認して、もう一度 setup.bat を実行してください。`nCould not install the packages. Check your internet connection and run setup.bat again." }
& $venvPy -m pip install --no-deps rtmlib==0.0.16 --disable-pip-version-check -q
if ($LASTEXITCODE -ne 0) { Fail "rtmlib のインストールに失敗しました。`nCould not install rtmlib." }
Ok 'パッケージ OK / Packages OK'

# ── 3. モデル ──
Say '骨格認識モデルをダウンロード中（約 360 MB、初回のみ）…'
Say 'Downloading the skeleton detection models (about 360 MB, first time only)...'
& $venvPy -m ddrpose.pose --download-models
if ($LASTEXITCODE -ne 0) { Fail "モデルのダウンロードに失敗しました。インターネット接続を確認して、もう一度 setup.bat を実行してください。`nCould not download the models. Check your internet connection and run setup.bat again." }
& $venvPy -c "from ddrpose.pose import ffmpeg_exe; ffmpeg_exe()"
if ($LASTEXITCODE -ne 0) { Fail "ffmpeg の準備に失敗しました。`nCould not set up ffmpeg." }
Ok 'モデル OK / Models OK'

# ── 4. ショートカット ──
if (-not $NoShortcut) {
  try {
    $desk = [Environment]::GetFolderPath('Desktop')
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path $desk 'DDR ステップ解析.lnk'))
    $lnk.TargetPath = Join-Path $Root 'start.bat'
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = "$env:SystemRoot\System32\imageres.dll,18"
    $lnk.Save()
    Ok 'デスクトップにショートカット「DDR ステップ解析」を作成しました。'
    Ok 'Created a desktop shortcut named "DDR ステップ解析" (DDR Step Analyzer).'
  } catch {
    Write-Host 'ショートカットを作成できませんでした（start.bat から起動できます）。' -ForegroundColor Yellow
    Write-Host 'Could not create the shortcut (you can start the app with start.bat).' -ForegroundColor Yellow
  }
}

Write-Host ''
Ok '=== セットアップ完了 / Setup complete ==='
Ok 'デスクトップの「DDR ステップ解析」（またはこのフォルダの start.bat）をダブルクリックすると起動します。'
Ok 'To start, double-click "DDR ステップ解析" on the desktop (or start.bat in this folder).'
