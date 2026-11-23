# Instala el runner de GitHub Actions en este PC para el bot de stock de PS5.
# Ejecutar en PowerShell COMO ADMINISTRADOR:
#   powershell -ExecutionPolicy Bypass -File .\instalar-runner-windows.ps1
# Te pedirá el token que aparece en:
#   https://github.com/shadowvivecoding-prog/ps5-stock-bot/settings/actions/runners/new
#   (línea "./config.cmd --url ... --token XXXXX"; caduca en 1 hora)

$ErrorActionPreference = "Stop"
$Repo = "https://github.com/shadowvivecoding-prog/ps5-stock-bot"
$Dir  = "C:\actions-runner"

Write-Host "== 1/4 Python y Git" -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue) -or (python --version 2>&1) -match "was not found") {
    winget install -e --id Python.Python.3.12 --scope machine --accept-source-agreements --accept-package-agreements
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
}
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")

Write-Host "== 2/4 Descargando el runner" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
Set-Location $Dir
$rel = Invoke-RestMethod "https://api.github.com/repos/actions/runner/releases/latest"
$asset = $rel.assets | Where-Object { $_.name -like "actions-runner-win-x64-*.zip" } | Select-Object -First 1
Invoke-WebRequest $asset.browser_download_url -OutFile runner.zip
Expand-Archive runner.zip -DestinationPath $Dir -Force
Remove-Item runner.zip

Write-Host "== 3/4 Registrando el runner en tu repo" -ForegroundColor Cyan
$token = Read-Host "Pega el token de GitHub (página 'New self-hosted runner')"
.\config.cmd --url $Repo --token $token --name "PC-casa" --labels "Windows" --unattended --replace

Write-Host "== 4/4 Arranque automático al iniciar sesión" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute "$Dir\run.cmd" -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "GitHub runner PS5" -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null
Start-ScheduledTask -TaskName "GitHub runner PS5"

Write-Host "`nListo. El runner 'PC-casa' arranca solo cada vez que inicias sesión en Windows." -ForegroundColor Green
Write-Host "Compruébalo en $Repo/settings/actions/runners (debe salir 'Idle')."
