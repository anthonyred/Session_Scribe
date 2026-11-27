# Session Scribe - Start Menu Shortcut Installer
# Creates or updates the "Session Scribe.lnk" shortcut in:
# %APPDATA%\Microsoft\Windows\Start Menu\Programs\

[CmdletBinding()]
param(
    [string]$AppRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$DestinationDir = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $DestinationDir)) {
    New-Item -ItemType Directory -Path $DestinationDir -Force | Out-Null
}

$shortcutPath = [System.IO.Path]::Combine($DestinationDir, "Session Scribe.lnk")
$targetPythonw = [System.IO.Path]::Combine($AppRoot, ".venv\Scripts\pythonw.exe")
$iconPath = [System.IO.Path]::Combine($AppRoot, "assets\icons\session_scribe.ico")

if (-not (Test-Path $targetPythonw)) {
    Write-Warning "Virtual environment pythonw.exe not found at: $targetPythonw"
    Write-Warning "Falling back to standard pythonw.exe or system path..."
    $sysPythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue)
    if ($sysPythonw) {
        $targetPythonw = $sysPythonw.Source
    }
}

if (-not (Test-Path $iconPath)) {
    Write-Warning "Icon file not found at: $iconPath"
}

Write-Host "Creating Windows Start Menu shortcut..."
Write-Host "  Shortcut Location : $shortcutPath"
Write-Host "  Target Executable : $targetPythonw"
Write-Host "  Arguments         : -m gui.app"
Write-Host "  Working Directory : $AppRoot"
Write-Host "  Icon Location     : $iconPath"

$wshShell = New-Object -ComObject WScript.Shell
$shortcut = $wshShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $targetPythonw
$shortcut.Arguments = "-m gui.app"
$shortcut.WorkingDirectory = $AppRoot
if (Test-Path $iconPath) {
    $shortcut.IconLocation = "$iconPath,0"
}
$shortcut.Description = "Session Scribe - Speech Transcription and Multi-Speaker Analysis"
$shortcut.Save()

[System.Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut) | Out-Null
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($wshShell) | Out-Null

Write-Host "[SUCCESS] Session Scribe Start Menu shortcut installed successfully." -ForegroundColor Green
