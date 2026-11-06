$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $root ".venv/Scripts/python.exe"
$module = Join-Path $root "code/copy_assistant"
$dist = Join-Path $root "dist"
$build = Join-Path $root "build"
$bundle = Join-Path $dist "CopyAssistant"

foreach ($path in @($dist, $build, $bundle)) {
    if ((Test-Path -LiteralPath $path) -and
        ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Build paths must be local directories inside the project: $path"
    }
}

Push-Location -LiteralPath $module
$previousPath = $env:PATH
try {
    $version = & $python -c "from config import VERSION; print(VERSION)"
    if ($LASTEXITCODE -ne 0) { throw "Could not read the application version." }
    & $python -c "import platform, struct; assert platform.machine().upper() == 'AMD64' and struct.calcsize('P') == 8"
    if ($LASTEXITCODE -ne 0) { throw "Build requires Windows x64 Python." }
    # Keep unrelated applications' DLLs out of the PyInstaller dependency scan.
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    & $python -m PyInstaller --noconfirm --onedir --windowed --noupx --name CopyAssistant `
        --exclude-module PySide6.QtNetwork --distpath $dist --workpath $build --specpath $build app.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
    $softwareOpenGL = Join-Path $bundle "_internal/PySide6/opengl32sw.dll"
    if (Test-Path -LiteralPath $softwareOpenGL) { Remove-Item -LiteralPath $softwareOpenGL }
    $translations = Join-Path $bundle "_internal/PySide6/translations"
    if (Test-Path -LiteralPath $translations) {
        Get-ChildItem -LiteralPath $translations -Filter "*.qm" -File | Remove-Item
    }
    & $python build_notices.py $bundle $build
    if ($LASTEXITCODE -ne 0) { throw "Could not collect third-party notices." }
}
finally {
    $env:PATH = $previousPath
    Pop-Location
}

Copy-Item -LiteralPath (Join-Path $root "LICENSE") -Destination $bundle
Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination $bundle
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = Join-Path $dist "CopyAssistant-v$version-windows-x64.zip"
$temporary = "$archive.tmp"
if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
[IO.Compression.ZipFile]::CreateFromDirectory(
    $bundle, $temporary, [IO.Compression.CompressionLevel]::Optimal, $true
)
Move-Item -LiteralPath $temporary -Destination $archive -Force
Write-Output $archive
