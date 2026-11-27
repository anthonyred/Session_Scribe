# Session Scribe - Start Menu Shortcut Uninstaller
# Removes "Session Scribe.lnk" from %APPDATA%\Microsoft\Windows\Start Menu\Programs\

[CmdletBinding()]
param(
    [string]$DestinationDir = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs")
)

$shortcutPath = [System.IO.Path]::Combine($DestinationDir, "Session Scribe.lnk")

if (Test-Path $shortcutPath) {
    Remove-Item -Path $shortcutPath -Force
    Write-Host "[SUCCESS] Removed Start Menu shortcut: $shortcutPath" -ForegroundColor Green
} else {
    Write-Host "[INFO] Shortcut does not exist: $shortcutPath (already clean)." -ForegroundColor Yellow
}
