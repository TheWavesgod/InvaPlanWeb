param(
    [Parameter(Mandatory = $true)][string]$RasterDir,
    [string]$PythonExe = "",
    [ValidateRange(1024, 65535)][int]$Port = 8765
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'dist\index.html') -PathType Leaf)) {
    throw "Run pnpm build from the InvaPlan folder before starting the demo."
}
if (-not $PythonExe) {
    $PythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw "Create the project's Python environment using the commands in README.md, or pass -PythonExe."
    }
}
$priorRasterDir = $env:INVAPLAN_RASTER_DIR
$env:INVAPLAN_RASTER_DIR = (Resolve-Path -LiteralPath $RasterDir).Path
Push-Location -LiteralPath $PSScriptRoot
try {
    & $PythonExe -B -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw "The InvaPlan server exited with code $LASTEXITCODE." }
}
finally {
    Pop-Location
    $env:INVAPLAN_RASTER_DIR = $priorRasterDir
}
