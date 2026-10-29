# Build ONE file, Saarthi.exe, with PyInstaller. It can be copied anywhere on its own.
#
#   .venv\Scripts\python -m pip install -e ".[build,screenreader]"
#   powershell -ExecutionPolicy Bypass -File scripts\build_pyinstaller.ps1
#
# Output: C:\Saarthi-build\Saarthi.exe (outside OneDrive: syncing the build files locks them
# and breaks the build). Change it with -OutDir. Takes a few minutes.
# The exe has the Saarthi logo (packaging\icon.ico). Each start unpacks it to a temporary folder,
# so the first window takes a few seconds.
# The Piper voices in -VoiceDir (*.onnx + *.onnx.json) are built into the exe, so the human
# voice works on any PC with no setup. A voice in %APPDATA%\Saarthi\voices still wins.
# Run it from a normal terminal, not from inside the Claude app: that app keeps its own private
# copy of %APPDATA%, so the voices would come from the wrong folder.

param([string]$OutDir = "C:\Saarthi-build", [string]$VoiceDir = "$env:APPDATA\Saarthi\voices")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"

$voices = @(Get-ChildItem $VoiceDir -Filter *.onnx -ErrorAction SilentlyContinue)
if ($voices.Count -eq 0) { throw "No Piper voices in $VoiceDir. Put the voice files there or pass -VoiceDir." }
Write-Host "Voices built in: $($voices.Name -join ', ')"
$vd = (Resolve-Path $VoiceDir).Path

# NVDA / JAWS output DLLs: accessible_output2 loads them from its own lib folder.
$ao2 = & $python -c "import accessible_output2, os; print(os.path.dirname(accessible_output2.__file__))"

$work = Join-Path $OutDir "work"
# Full paths: PyInstaller reads relative ones from its work folder.
& $python -m PyInstaller "$root\packaging\entry.py" `
    --noconfirm --clean `
    --onefile `
    --windowed `
    --name Saarthi `
    --icon "$root\packaging\icon.ico" `
    --version-file "$root\packaging\version_info.txt" `
    --distpath $OutDir `
    --workpath $work `
    --specpath $work `
    --paths $root `
    --add-data "$root\app\audio\data;app\audio\data" `
    --add-data "$root\packaging\icon.ico;packaging" `
    --add-data "$ao2\lib;accessible_output2\lib" `
    --add-data "$vd\*.onnx;voices" `
    --add-data "$vd\*.onnx.json;voices" `
    --collect-submodules app `
    --collect-submodules comtypes `
    --collect-all piper `
    --hidden-import pyaudiowpatch `
    --exclude-module pytest `
    --exclude-module jiwer `
    --exclude-module tkinter `
    --exclude-module comtypes.test
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

Write-Host "Built: $OutDir\Saarthi.exe"
