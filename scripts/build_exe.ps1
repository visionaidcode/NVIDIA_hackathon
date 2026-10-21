# Build dist\MeetingAssistant\MeetingAssistant.exe and a zip to share.
#
#   .venv\Scripts\python -m pip install -e ".[build]"
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
#
# The exe reads .env from its own folder. The zip includes .env.example, never .env.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"

& $python -m PyInstaller --noconfirm --clean --onefile --console `
    --name MeetingAssistant `
    --paths $root `
    --add-data "$root\app\audio\data;app\audio\data" `
    --collect-submodules comtypes `
    --exclude-module torch --exclude-module torchaudio --exclude-module silero_vad `
    --exclude-module jiwer --exclude-module pytest --exclude-module tkinter --exclude-module comtypes.test `
    --distpath dist\MeetingAssistant --workpath build --specpath build `
    packaging\entry.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$out = Join-Path $root "dist\MeetingAssistant"
Copy-Item .env.example (Join-Path $out ".env.example") -Force
Copy-Item README.md, LICENSE $out -Force

$zip = Join-Path $root "dist\MeetingAssistant-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $out "MeetingAssistant.exe"), (Join-Path $out ".env.example"), `
    (Join-Path $out "README.md"), (Join-Path $out "LICENSE") -DestinationPath $zip

$exe = Get-Item (Join-Path $out "MeetingAssistant.exe")
"Built $($exe.FullName) ($([math]::Round($exe.Length / 1MB, 1)) MB)"
"Zip:  $zip"
