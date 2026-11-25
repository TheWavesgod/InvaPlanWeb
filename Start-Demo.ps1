[CmdletBinding(DefaultParameterSetName = 'Release')]
param(
    [Parameter(ParameterSetName = 'Release')][string]$ReleaseDir,
    [Parameter(ParameterSetName = 'Release')][string]$AreaDir,
    [Parameter(Mandatory = $true, ParameterSetName = 'Raster')][string]$RasterDir,
    [string]$ClimateDir = "",
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
$priorReleaseDir = $env:INVAPLAN_RELEASE_DIR
$priorClimateDir = $env:INVAPLAN_CLIMATE_DIR
$priorAreaDir = $env:INVAPLAN_AREA_DIR
Push-Location -LiteralPath $PSScriptRoot
try {
    if ($ClimateDir) { $env:INVAPLAN_CLIMATE_DIR = (Resolve-Path -LiteralPath $ClimateDir).Path }
    if ($PSCmdlet.ParameterSetName -eq 'Raster') {
        $env:INVAPLAN_RELEASE_DIR = $null
        $env:INVAPLAN_RASTER_DIR = (Resolve-Path -LiteralPath $RasterDir).Path
    }
    else {
        if (-not $ReleaseDir) {
            $ReleaseDir = Join-Path $PSScriptRoot 'data\jiaxing-39'
            if (-not (Test-Path -LiteralPath (Join-Path $ReleaseDir 'manifest.json') -PathType Leaf)) {
                $ReleaseDir = Join-Path $PSScriptRoot 'data\jiaxing-sample'
            }
        }
        $env:INVAPLAN_RASTER_DIR = $null
        $env:INVAPLAN_RELEASE_DIR = (Resolve-Path -LiteralPath $ReleaseDir).Path
        if ($AreaDir) {
            $env:INVAPLAN_AREA_DIR = (Resolve-Path -LiteralPath $AreaDir).Path
        }
        elseif (-not $env:INVAPLAN_AREA_DIR -and $env:LOCALAPPDATA -and
                $env:INVAPLAN_RELEASE_DIR -eq (Join-Path $PSScriptRoot 'data\jiaxing-39') -and
                -not (Test-Path -LiteralPath (Join-Path $ReleaseDir 'area-cities.json') -PathType Leaf)) {
            $localArea = Join-Path $env:LOCALAPPDATA 'InvaPlan\area-cities'
            if (Test-Path -LiteralPath (Join-Path $localArea 'area-cities.json') -PathType Leaf) {
                $env:INVAPLAN_AREA_DIR = $localArea
            }
        }
    }
    & $PythonExe -B -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw "The InvaPlan server exited with code $LASTEXITCODE." }
}
finally {
    Pop-Location
    $env:INVAPLAN_RASTER_DIR = $priorRasterDir
    $env:INVAPLAN_RELEASE_DIR = $priorReleaseDir
    $env:INVAPLAN_CLIMATE_DIR = $priorClimateDir
    $env:INVAPLAN_AREA_DIR = $priorAreaDir
}
