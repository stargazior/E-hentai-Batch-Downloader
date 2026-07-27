param(
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

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

$PythonExe = Resolve-Python -Requested $Python
Set-Location $Root
$env:PYGAME_HIDE_SUPPORT_PROMPT = "1"

& $PythonExe -m py_compile eh_batch_downloader.py eh_batch_gui.py
& $PythonExe eh_batch_downloader.py --self-test
& $PythonExe -m unittest discover -s tests
