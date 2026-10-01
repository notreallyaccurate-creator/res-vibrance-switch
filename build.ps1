# Builds:
#   dist\ResVibranceSwitch.exe                    portable single-file exe
#   dist\ResVibranceSwitch-Setup-<version>.exe    installer (needs Inno Setup 6)
#
# Usage: powershell -ExecutionPolicy Bypass -File build.ps1
#
# Optional code signing (removes "unknown publisher" warnings once your certificate has reputation):
#   $env:SIGN_PFX = "C:\path\to\certificate.pfx"; $env:SIGN_PASSWORD = "..."
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install --quiet pyinstaller pystray pillow customtkinter
$version = (python -c "from version import __version__; print(__version__)").Trim()
Write-Host "Building version $version"

# Icon and Windows file-properties resource
python -c "import app; app.make_icon_image(256).save('icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
$v = ($version.Split(".") + @("0", "0", "0"))[0..3] -join ", "
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($v), prodvers=($v), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Res & Vibrance Switch'),
      StringStruct('FileDescription', 'Res & Vibrance Switch'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', 'ResVibranceSwitch'),
      StringStruct('OriginalFilename', 'ResVibranceSwitch.exe'),
      StringStruct('ProductName', 'Res & Vibrance Switch'),
      StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding ascii version_info.txt

$common = @("--noconfirm", "--clean", "--windowed", "--name", "ResVibranceSwitch", "--icon", "icon.ico",
            "--version-file", "version_info.txt", "--hidden-import", "pystray._win32",
            "--collect-data", "customtkinter", "app.py")

Write-Host "`n== Portable exe"
python -m PyInstaller --onefile --distpath dist --workpath build\onefile @common
if ($LASTEXITCODE) { throw "PyInstaller (onefile) failed" }

Write-Host "`n== Installer files"
python -m PyInstaller --onedir --distpath dist-installer --workpath build\onedir @common
if ($LASTEXITCODE) { throw "PyInstaller (onedir) failed" }

function Sign($file) {
    if (-not $env:SIGN_PFX) { return }
    $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
        Select-Object -Last 1
    if (-not $signtool) { Write-Warning "SIGN_PFX is set but signtool.exe (Windows SDK) wasn't found - skipping"; return }
    & $signtool.FullName sign /f $env:SIGN_PFX /p $env:SIGN_PASSWORD /fd sha256 /tr http://timestamp.digicert.com /td sha256 $file
    if ($LASTEXITCODE) { throw "Signing $file failed" }
}
Sign "dist\ResVibranceSwitch.exe"
Sign "dist-installer\ResVibranceSwitch\ResVibranceSwitch.exe"

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    Write-Host "`n== Installer"
    & $iscc /Q "/DAppVersion=$version" installer.iss
    if ($LASTEXITCODE) { throw "Inno Setup failed" }
    Sign "dist\ResVibranceSwitch-Setup-$version.exe"
} else {
    Write-Warning "Inno Setup 6 not found - skipping the installer (winget install JRSoftware.InnoSetup)"
}

Write-Host "`nDone:"
Get-ChildItem dist\*.exe | ForEach-Object { "  {0}  ({1:N1} MB)" -f $_.FullName, ($_.Length / 1MB) }
