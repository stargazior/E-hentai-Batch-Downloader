param(
  [string]$Python = "",
  [switch]$InstallDeps,
  [switch]$OneFile
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$AppName = "EHBatchDownloader"
$ReleaseDir = Join-Path $Root "dist_release"

function Resolve-Python {
  param([string]$Requested)
  if ($Requested) {
    if (Test-Path $Requested) {
      return (Resolve-Path -LiteralPath $Requested).Path
    }
    $RequestedCommand = Get-Command $Requested -ErrorAction SilentlyContinue
    if ($RequestedCommand) {
      return $RequestedCommand.Source
    }
    return $Requested
  }

  $CondaPython = "C:\Users\hoshizora\.conda\envs\pytorch\python.exe"
  if (Test-Path $CondaPython) {
    return $CondaPython
  }

  $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($PyLauncher) {
    return "py"
  }

  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($PythonCommand) {
    return "python"
  }

  throw "Python was not found. Pass -Python C:\Path\To\python.exe."
}

function Assert-In-Root {
  param([string]$PathToCheck)
  $ResolvedRoot = [System.IO.Path]::GetFullPath($Root)
  $ResolvedPath = [System.IO.Path]::GetFullPath($PathToCheck)
  if (-not $ResolvedPath.StartsWith($ResolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean path outside project root: $ResolvedPath"
  }
}

$PythonExe = Resolve-Python -Requested $Python
Set-Location $Root
$env:PYGAME_HIDE_SUPPORT_PROMPT = "1"

$PythonHome = Split-Path -Parent ([System.IO.Path]::GetFullPath($PythonExe))
$CondaLibraryBin = Join-Path $PythonHome "Library\bin"
$PythonDllDir = Join-Path $PythonHome "DLLs"
$PathParts = @($PythonHome, $CondaLibraryBin, $PythonDllDir) | Where-Object { Test-Path $_ }
if ($PathParts.Count -gt 0) {
  $env:PATH = ($PathParts -join ";") + ";" + $env:PATH
}

try {
  & $PythonExe -c "import PyInstaller" | Out-Null
} catch {
  if (-not $InstallDeps) {
    throw "PyInstaller is not installed. Re-run with -InstallDeps or install requirements-build.txt."
  }
  & $PythonExe -m pip install -r requirements-build.txt
}

& $PSScriptRoot\run_tests.ps1 -Python $PythonExe

$BuildRoot = Join-Path $Root "build"
$WorkPath = Join-Path $BuildRoot "pyinstaller-work"
$DistPath = Join-Path $BuildRoot "pyinstaller-dist"
$SpecPath = Join-Path $BuildRoot "pyinstaller-spec"
foreach ($PathToClean in @($WorkPath, $DistPath, $SpecPath)) {
  Assert-In-Root -PathToCheck $PathToClean
  if (Test-Path $PathToClean) {
    Remove-Item -LiteralPath $PathToClean -Recurse -Force
  }
}
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
New-Item -ItemType Directory -Force -Path $SpecPath | Out-Null

$PyInstallerArgs = @(
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--console",
  "--name", $AppName,
  "--workpath", $WorkPath,
  "--distpath", $DistPath,
  "--specpath", $SpecPath
)
if ($OneFile) {
  $PyInstallerArgs += "--onefile"
} else {
  $PyInstallerArgs += "--onedir"
}

$RequiredDlls = @(
  "libcrypto-3-x64.dll",
  "libssl-3-x64.dll",
  "libmpdec-4.dll",
  "libbz2.dll",
  "bzip2.dll",
  "ffi.dll",
  "ffi-8.dll",
  "tcl86t.dll",
  "tk86t.dll"
)
foreach ($DllName in $RequiredDlls) {
  $DllPath = Join-Path $CondaLibraryBin $DllName
  if (Test-Path $DllPath) {
    $PyInstallerArgs += @("--add-binary", "$DllPath;.")
  }
}

$PyInstallerArgs += "eh_batch_gui.py"

& $PythonExe @PyInstallerArgs

$ZipPath = Join-Path $ReleaseDir "$AppName-windows-x64.zip"
if (Test-Path $ZipPath) {
  Remove-Item -LiteralPath $ZipPath -Force
}

if ($OneFile) {
  $ExePath = Join-Path $DistPath "$AppName.exe"
  Compress-Archive -Path $ExePath -DestinationPath $ZipPath
} else {
  $AppDir = Join-Path $DistPath $AppName
  Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $ZipPath
}

Write-Host "Built $ZipPath"
