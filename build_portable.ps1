$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$AppName = "PerfumeStudio"
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$PortableDir = Join-Path $PSScriptRoot "dist\$AppName"
$OldUserData = Join-Path $PortableDir "user_data"
$BackupUserData = Join-Path $PSScriptRoot ".build_user_data_backup"
$ReleaseDir = Join-Path $PSScriptRoot "release"
$StageRoot = Join-Path $PSScriptRoot ".portable_zip_stage"
$StageApp = Join-Path $StageRoot $AppName
$ZipPath = Join-Path $ReleaseDir "${AppName}_Portable.zip"

Write-Host ""
Write-Host "=== PerfumeStudio Portable Builder ==="
Write-Host ""

# Keep the local dist copy's user data safe while rebuilding. The distributable ZIP
# intentionally receives an EMPTY user_data directory so private inventory/formulas
# are never accidentally shipped inside a release.
if (Test-Path $BackupUserData) { Remove-Item $BackupUserData -Recurse -Force }
if (Test-Path $OldUserData) {
    Write-Host "Backing up existing dist user_data..."
    Copy-Item $OldUserData $BackupUserData -Recurse -Force
}

if (!(Test-Path $Python)) {
    Write-Host "Creating Python virtual environment..."
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create Python virtual environment." }
}

Write-Host "Installing/updating build dependencies..."
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to update pip." }
& $Python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Failed to install requirements." }

# Best-effort OCR setup before packaging. If Tesseract can be found/installed here,
# its Windows files are copied into the portable package below.
Write-Host "Checking Tesseract OCR..."
& $Python -c "from perfume_studio.services.legacy_gcms_ocr import configure_tesseract,_try_install_tesseract_windows; print('Tesseract ready' if (configure_tesseract() or _try_install_tesseract_windows()) else 'Tesseract not found; portable build will continue without bundled OCR binary.')"

if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
if (Test-Path "$AppName.spec") { Remove-Item "$AppName.spec" -Force }

$PyInstallerArgs = @(
    "--noconfirm", "--clean", "--windowed", "--onedir",
    "--name", $AppName,
    "--collect-all", "PySide6",
    "--add-data", "data;data"
)

if (Test-Path "Tesseract-OCR\tesseract.exe") {
    $PyInstallerArgs += @("--add-data", "Tesseract-OCR;Tesseract-OCR")
} elseif (Test-Path "Tesseract\tesseract.exe") {
    $PyInstallerArgs += @("--add-data", "Tesseract;Tesseract-OCR")
} elseif (Test-Path "tesseract\tesseract.exe") {
    $PyInstallerArgs += @("--add-data", "tesseract;Tesseract-OCR")
}
$PyInstallerArgs += "app.py"

Write-Host "Building Windows portable application..."
& $Python -m PyInstaller @PyInstallerArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

$ExePath = Join-Path $PortableDir "$AppName.exe"
if (!(Test-Path $ExePath)) {
    throw "Build finished but $AppName.exe was not created: $ExePath"
}

# If Tesseract is installed on the build PC but was not supplied as a project folder,
# copy the installed distribution beside the EXE so OCR works on another PC too.
$TesseractSources = @(
    (Join-Path $PSScriptRoot "Tesseract-OCR"),
    (Join-Path $PSScriptRoot "Tesseract"),
    (Join-Path $PSScriptRoot "tesseract"),
    (Join-Path $env:ProgramFiles "Tesseract-OCR")
)
if (${env:ProgramFiles(x86)}) {
    $TesseractSources += (Join-Path ${env:ProgramFiles(x86)} "Tesseract-OCR")
}
$TesseractSource = $TesseractSources | Where-Object { $_ -and (Test-Path (Join-Path $_ "tesseract.exe")) } | Select-Object -First 1
if ($TesseractSource) {
    $TesseractTarget = Join-Path $PortableDir "Tesseract-OCR"
    if (Test-Path $TesseractTarget) { Remove-Item $TesseractTarget -Recurse -Force }
    Write-Host "Bundling Tesseract OCR from: $TesseractSource"
    Copy-Item $TesseractSource $TesseractTarget -Recurse -Force
} else {
    Write-Warning "Tesseract OCR binary was not found. PDF/image OCR in the generated ZIP will require Tesseract on the target PC."
}

# Restore the user's local dist data after the clean rebuild.
if (Test-Path $BackupUserData) {
    Write-Host "Restoring local dist user_data..."
    Copy-Item $BackupUserData $OldUserData -Recurse -Force
    Remove-Item $BackupUserData -Recurse -Force
} elseif (!(Test-Path $OldUserData)) {
    New-Item -ItemType Directory -Path $OldUserData | Out-Null
}

# Build a clean distributable ZIP. Do NOT include the local user's database/formulas.
if (Test-Path $StageRoot) { Remove-Item $StageRoot -Recurse -Force }
New-Item -ItemType Directory -Path $StageApp -Force | Out-Null
Get-ChildItem $PortableDir -Force | Where-Object { $_.Name -ne 'user_data' } | ForEach-Object {
    Copy-Item $_.FullName $StageApp -Recurse -Force
}
New-Item -ItemType Directory -Path (Join-Path $StageApp "user_data") -Force | Out-Null

if (!(Test-Path $ReleaseDir)) { New-Item -ItemType Directory -Path $ReleaseDir | Out-Null }
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Write-Host "Creating portable ZIP..."
Compress-Archive -Path $StageApp -DestinationPath $ZipPath -CompressionLevel Optimal
Remove-Item $StageRoot -Recurse -Force

Write-Host ""
Write-Host "========================================"
Write-Host " BUILD COMPLETE"
Write-Host "========================================"
Write-Host "Executable: $ExePath"
Write-Host "Portable ZIP: $ZipPath"
Write-Host ""
