param(
    [Parameter(Mandatory = $true)][string]$GgufDirectory,
    [string]$OnnxDirectory = '',
    [string]$WorkDirectory = (Join-Path $PSScriptRoot 'work'),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'output')
)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
Remove-Item Env:MINERU_PORTABLE_OFFLINE -ErrorAction SilentlyContinue
$WorkDirectory = [System.IO.Path]::GetFullPath($WorkDirectory)
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$GgufDirectory = (Resolve-Path -LiteralPath $GgufDirectory).Path
$cache = Join-Path $WorkDirectory 'uv-cache'
New-Item -ItemType Directory -Force -Path $WorkDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
foreach ($name in @('MinerU2.5-Pro-2605-1.2B-Q4_K_M.gguf', 'mmproj-MinerU2.5-Pro-2605-1.2B-f16.gguf')) {
    if (-not (Test-Path -LiteralPath (Join-Path $GgufDirectory $name))) { throw "缺少模型文件：$name" }
}

function Invoke-Uv {
    & uv @args
    if ($LASTEXITCODE -ne 0) { throw 'uv 构建步骤失败' }
}
Invoke-Uv python install cpython-3.12.13-windows-x86_64-none --install-dir (Join-Path $WorkDirectory 'managed-python') --cache-dir $cache --no-bin
Invoke-Uv python install cpython-3.12.13-linux-x86_64-gnu --install-dir (Join-Path $WorkDirectory 'managed-python-linux') --cache-dir $cache --no-bin
$python = Join-Path $WorkDirectory 'managed-python/cpython-3.12.13-windows-x86_64-none/python.exe'
function Invoke-BuilderPython {
    & $python @args
    if ($LASTEXITCODE -ne 0) { throw 'Python 构建步骤失败' }
}
foreach ($platform in @('windows', 'linux')) {
    $wheelPlatform = if ($platform -eq 'windows') { 'windows' } else { 'x86_64-manylinux_2_28' }
    Invoke-Uv pip install --python-version 3.12 --python-platform $wheelPlatform --target (Join-Path $WorkDirectory "stage/$platform/packages") --no-python-downloads --link-mode copy --cache-dir $cache -r (Join-Path $PSScriptRoot "requirements-$platform.lock")
}
if ($OnnxDirectory) {
    $OnnxDirectory = (Resolve-Path -LiteralPath $OnnxDirectory).Path
} else {
    $env:PYTHONPATH = Join-Path $WorkDirectory 'stage/windows/packages'
    Invoke-BuilderPython (Join-Path $PSScriptRoot 'download_models.py') --dest (Join-Path $WorkDirectory 'shared-models')
    Remove-Item Env:PYTHONPATH
    $OnnxDirectory = Join-Path $WorkDirectory 'shared-models/MinerU-4_models_onnx'
}
$debian = 'https://archive.debian.org/debian/pool/main/g/gcc-8/'
foreach ($item in @(
    @('libstdc++6_8.3.0-6_amd64.deb', 'libstdcpp.deb'),
    @('libgcc1_8.3.0-6_amd64.deb', 'libgcc.deb'),
    @('gcc-8-base_8.3.0-6_amd64.deb', 'gcc8-base.deb')
)) {
    $archive = Join-Path $WorkDirectory $item[1]
    if (-not (Test-Path -LiteralPath $archive)) {
        Invoke-WebRequest -Uri ($debian + $item[0]) -OutFile $archive
    }
}
Invoke-BuilderPython (Join-Path $PSScriptRoot 'assemble.py') --work $WorkDirectory --gguf-dir $GgufDirectory --onnx-dir $OnnxDirectory
Invoke-BuilderPython (Join-Path $PSScriptRoot 'bundle_libraries.py') --work $WorkDirectory
Invoke-BuilderPython (Join-Path $PSScriptRoot 'audit_linux.py') (Join-Path $WorkDirectory 'stage/linux') --output (Join-Path $OutputDirectory 'linux-elf-audit.json')
Copy-Item -LiteralPath (Join-Path $OutputDirectory 'linux-elf-audit.json') -Destination (Join-Path $WorkDirectory 'stage/linux/linux-elf-audit.json')
$env:PYTHONPATH = Join-Path $WorkDirectory 'stage/windows/packages'
Invoke-BuilderPython -m unittest discover -s (Join-Path $PSScriptRoot 'tests') -v
Remove-Item Env:PYTHONPATH
Invoke-BuilderPython (Join-Path $WorkDirectory 'stage/windows/launcher.py') --check
Invoke-BuilderPython (Join-Path $PSScriptRoot 'package.py') --work $WorkDirectory --output $OutputDirectory --gguf-dir $GgufDirectory
