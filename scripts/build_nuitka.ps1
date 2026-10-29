# Build ONE file, Saarthi.exe, with Nuitka. It can be copied anywhere on its own.
#
#   .venv\Scripts\python -m pip install nuitka ordered-set zstandard
#   powershell -ExecutionPolicy Bypass -File scripts\build_nuitka.ps1
#
# Output: C:\Saarthi-build\Saarthi.exe (outside OneDrive: syncing
# the thousands of build files locks them and breaks the build). Change it with -OutDir.
# On its first start the exe unpacks itself once to
# %LOCALAPPDATA%\VisionAid\Saarthi\1.0.0 and reuses that on later starts.
# Run it from a normal terminal, not from inside the Claude app: that app keeps its own private
# copy of %APPDATA%, so the voices would come from the wrong folder.
# First build takes 1-2 hours (numpy, onnxruntime, wxPython, openai are compiled to C);
# later builds reuse the ccache. Without Visual Studio, Nuitka downloads MinGW itself.
# The Piper voices in -VoiceDir (*.onnx + *.onnx.json) are built into the exe, so the human
# voice works on any PC with no setup. A voice in %APPDATA%\Saarthi\voices still wins.

param([string]$OutDir = "C:\Saarthi-build", [string]$VoiceDir = "$env:APPDATA\Saarthi\voices")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"

$voices = @(Get-ChildItem $VoiceDir -Filter *.onnx -ErrorAction SilentlyContinue)
if ($voices.Count -eq 0) { throw "No Piper voices in $VoiceDir. Put the voice files there or pass -VoiceDir." }
Write-Host "Voices built in: $($voices.Name -join ', ')"
$vd = (Resolve-Path $VoiceDir).Path.Replace("\", "/")

# NVDA / JAWS output DLLs: not package data to Nuitka; accessible_output2 loads them from its lib folder.
# Forward slashes: Nuitka's file pattern does not match "*.dll" after a backslash path.
$ao2 = & $python -c "import accessible_output2, os; print(os.path.dirname(accessible_output2.__file__).replace(os.sep, '/'))"

& $python -m nuitka packaging\entry.py `
    --onefile `
    --onefile-tempdir-spec="{CACHE_DIR}/VisionAid/Saarthi/1.0.0" `
    --assume-yes-for-downloads `
    --windows-console-mode=disable `
    --windows-icon-from-ico=packaging\icon.ico `
    --output-dir=$OutDir `
    --output-filename=Saarthi.exe `
    --include-data-dir=app\audio\data=app\audio\data `
    --include-data-files="$ao2/lib/*.dll=accessible_output2/lib/" `
    --include-data-files="$vd/*.onnx=voices/" `
    --include-data-files="$vd/*.onnx.json=voices/" `
    --include-package=app `
    --include-package=comtypes `
    --include-package=openai `
    --include-package-data=piper `
    --nofollow-import-to=comtypes.test `
    --nofollow-import-to=pytest `
    --nofollow-import-to=jiwer `
    --nofollow-import-to=tkinter `
    --company-name="VisionAid" `
    --product-name="Saarthi" `
    --file-description="Saarthi" `
    --file-version=1.0.0 `
    --product-version=1.0.0 `
    --copyright="VisionAid"
if ($LASTEXITCODE -ne 0) { throw "Nuitka build failed" }

Write-Host "Built: $OutDir\Saarthi.exe"
