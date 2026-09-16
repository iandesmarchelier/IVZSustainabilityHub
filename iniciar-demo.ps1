$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if ((Test-Path -LiteralPath $bundledPython) -and (Test-Path -LiteralPath '.deps')) {
    $env:PYTHONPATH = Join-Path $PSScriptRoot '.deps'
    & $bundledPython -m backend.demo
    & $bundledPython -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
} else {
    Write-Host 'Seguí las instrucciones de instalación de README.md usando Python.'
}
