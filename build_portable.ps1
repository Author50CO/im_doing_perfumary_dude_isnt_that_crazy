$ErrorActionPreference = "Stop"

Write-Host "========================================"
Write-Host "PerfumeCalculator Portable Build"
Write-Host "========================================"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$AppName = "PerfumeCalculator"
$DistRoot = Join-Path $ProjectRoot "dist"
$BuildRoot = Join-Path $ProjectRoot "build"
$SpecFile = Join-Path $ProjectRoot "$AppName.spec"
$PortableDir = Join-Path $DistRoot $AppName
$ZipPath = Join-Path $ProjectRoot "${AppName}_portable.zip"

Write-Host ""
Write-Host "Project root:"
Write-Host $ProjectRoot

Write-Host ""
Write-Host "Checking files..."

if (!(Test-Path (Join-Path $ProjectRoot "run.py"))) {
    throw "run.py not found. This script must be next to run.py."
}

if (!(Test-Path (Join-Path $ProjectRoot "perfume_tool"))) {
    throw "perfume_tool folder not found."
}

if (!(Test-Path (Join-Path $ProjectRoot "perfume_tool\app.py"))) {
    throw "perfume_tool\app.py not found."
}

if (!(Test-Path (Join-Path $ProjectRoot "tesseract\tesseract.exe"))) {
    throw "tesseract\tesseract.exe not found. Copy C:\Program Files\Tesseract-OCR into project as tesseract\ first."
}

if (!(Test-Path (Join-Path $ProjectRoot "tesseract\tessdata\eng.traineddata"))) {
    throw "tesseract\tessdata\eng.traineddata not found."
}

Write-Host "OK."

Write-Host ""
Write-Host "Installing / checking requirements..."

python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed."
}

python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "pip install -r requirements.txt failed."
}

Write-Host ""
Write-Host "Cleaning old build files..."

if (Test-Path $DistRoot) {
    Remove-Item $DistRoot -Recurse -Force
}

if (Test-Path $BuildRoot) {
    Remove-Item $BuildRoot -Recurse -Force
}

if (Test-Path $SpecFile) {
    Remove-Item $SpecFile -Force
}

if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

Write-Host ""
Write-Host "Running PyInstaller..."

$PyInstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", $AppName,
    "--distpath", $DistRoot,
    "--workpath", $BuildRoot,
    "--specpath", $ProjectRoot,

    "--add-data", "tesseract;tesseract",

    "--hidden-import", "PIL._tkinter_finder",
    "--hidden-import", "pytesseract",
    "--hidden-import", "tksheet",
    "--hidden-import", "cv2",
    "--hidden-import", "numpy",
    "--hidden-import", "xml.etree.ElementTree",

    "--exclude-module", "matplotlib",
    "--exclude-module", "matplotlib.pyplot",
    "--exclude-module", "pandas",
    "--exclude-module", "scipy",
    "--exclude-module", "sklearn",
    "--exclude-module", "IPython",
    "--exclude-module", "jupyter",
    "--exclude-module", "notebook",

    "run.py"
)

Write-Host "python $($PyInstallerArgs -join ' ')"
python @PyInstallerArgs

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE. Look at the error messages above this line."
}

Write-Host ""
Write-Host "Listing dist folder..."

if (Test-Path $DistRoot) {
    Get-ChildItem $DistRoot -Force | Format-Table Name, Mode, Length
} else {
    throw "dist folder was not created."
}

if (!(Test-Path $PortableDir)) {
    Write-Host ""
    Write-Host "Expected portable directory was not found:"
    Write-Host $PortableDir

    Write-Host ""
    Write-Host "Trying to find generated exe..."

    $FoundExe = Get-ChildItem $DistRoot -Recurse -Filter "$AppName.exe" -ErrorAction SilentlyContinue | Select-Object -First 1

    if ($FoundExe -eq $null) {
        throw "Could not find $AppName.exe anywhere under dist. PyInstaller probably did not build the app."
    }

    $PortableDir = Split-Path -Parent $FoundExe.FullName

    Write-Host "Found exe at:"
    Write-Host $FoundExe.FullName

    Write-Host "Using portable directory:"
    Write-Host $PortableDir
}

Write-Host ""
Write-Host "Creating formulas folder next to exe..."

$FormulaDir = Join-Path $PortableDir "formulas"
if (!(Test-Path $FormulaDir)) {
    New-Item -ItemType Directory -Path $FormulaDir | Out-Null
}

Write-Host ""
Write-Host "Verifying build output..."

$ExePath = Join-Path $PortableDir "$AppName.exe"

if (!(Test-Path $ExePath)) {
    throw "Exe not found: $ExePath"
}

$TesseractPath1 = Join-Path $PortableDir "_internal\tesseract\tesseract.exe"
$TesseractPath2 = Join-Path $PortableDir "tesseract\tesseract.exe"

if ((Test-Path $TesseractPath1) -or (Test-Path $TesseractPath2)) {
    Write-Host "Bundled Tesseract found."
} else {
    throw "Bundled Tesseract not found in portable output."
}

Write-Host ""
Write-Host "Creating portable zip..."

if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

Compress-Archive -Path $PortableDir -DestinationPath $ZipPath -Force

Write-Host ""
Write-Host "========================================"
Write-Host "Build complete!"
Write-Host "========================================"
Write-Host "Portable folder:"
Write-Host $PortableDir
Write-Host ""
Write-Host "Portable zip:"
Write-Host $ZipPath
Write-Host ""
Write-Host "Run:"
Write-Host $ExePath
Write-Host "========================================"