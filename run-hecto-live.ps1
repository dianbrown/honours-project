[CmdletBinding()]
param(
    [string]$Uri = "",
    [string]$Port = "",
    [int]$Antenna = 1,
    [int]$ReadPower = 1900,
    [string]$ReadAsyncPath = "",
    [string]$Toolset = "",
    [string]$WindowsSdkVersion = "",
    [switch]$Rebuild,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"

function Resolve-Msbuild {
    $cmd = Get-Command msbuild -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $path = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find "MSBuild\**\Bin\MSBuild.exe" | Select-Object -First 1
        if ($path) {
            return $path
        }
    }

    $fallback = Get-ChildItem "$env:ProgramFiles\Microsoft Visual Studio\*\*\MSBuild\Current\Bin\MSBuild.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($fallback) {
        return $fallback.FullName
    }

    return $null
}

function Resolve-PlatformToolset {
    param(
        [string]$MsbuildPath,
        [string]$RequestedToolset
    )

    if ($RequestedToolset) {
        return $RequestedToolset
    }

    $msbuildBin = Split-Path -Parent $MsbuildPath
    $vcRoot = Resolve-Path (Join-Path $msbuildBin "..\..\Microsoft\VC") -ErrorAction SilentlyContinue
    if (-not $vcRoot) {
        return "v143"
    }

    $schemaVersions = Get-ChildItem $vcRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "^v\d+$" } |
        Sort-Object { [int]($_.Name.TrimStart("v")) } -Descending

    foreach ($schema in $schemaVersions) {
        $toolsetDir = Join-Path $schema.FullName "Platforms\Win32\PlatformToolsets"
        if (-not (Test-Path $toolsetDir)) {
            continue
        }
        $toolsets = Get-ChildItem $toolsetDir -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "^v\d+$" } |
            Sort-Object { [int]($_.Name.TrimStart("v")) } -Descending
        if ($toolsets -and $toolsets.Count -gt 0) {
            return $toolsets[0].Name
        }
    }

    return "v143"
}

function Resolve-WindowsSdkVersion {
    param(
        [string]$RequestedSdkVersion
    )

    if ($RequestedSdkVersion) {
        return $RequestedSdkVersion
    }

    $sdkRoot = "${env:ProgramFiles(x86)}\Windows Kits\10\Include"
    $sdkDirs = Get-ChildItem $sdkRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "^\d+\.\d+\.\d+\.\d+$" } |
        Sort-Object { [Version]$_.Name } -Descending

    if ($sdkDirs -and $sdkDirs.Count -gt 0) {
        return $sdkDirs[0].Name
    }

    return ""
}

function Resolve-ReaderUri {
    param(
        [string]$InUri,
        [string]$InPort
    )

    if ($InUri) {
        if ($InUri -like "tmr://*" -or $InUri -like "llrp://*") {
            return $InUri
        }
        if ($InUri -match "^COM\d+$") {
            return "tmr:///$InUri"
        }
        return "tmr:///$InUri"
    }

    if ($InPort) {
        if ($InPort -match "^COM\d+$") {
            return "tmr:///$InPort"
        }
        return "tmr:///$InPort"
    }

    $ports = [System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object
    if (-not $ports -or $ports.Count -eq 0) {
        throw "No serial COM ports detected. Plug in the Hecto M7E and retry with -Port COMx."
    }

    return "tmr:///$($ports[0])"
}

function Resolve-ReadAsyncExe {
    param(
        [string]$Root,
        [string]$RequestedPath
    )

    if ($RequestedPath) {
        if (Test-Path $RequestedPath) {
            return (Resolve-Path $RequestedPath).Path
        }
        throw "Specified -ReadAsyncPath not found: $RequestedPath"
    }

    $candidates = @(
        (Join-Path $Root "c\projVS2019\Samples\ReadAsync-Release\ReadAsync.exe"),
        (Join-Path $Root "c\projVS2019\Samples\ReadAsync-Release\readasync.exe"),
        (Join-Path $Root "c\proj\Samples\ReadAsync-Release\ReadAsync.exe"),
        (Join-Path $Root "c\proj\Samples\ReadAsync-Release\readasync.exe"),
        (Join-Path $Root "c\src\api\readasync.exe")
    )

    foreach ($p in $candidates) {
        if (Test-Path $p) {
            return (Resolve-Path $p).Path
        }
    }

    return $null
}

function Invoke-Msbuild {
    param(
        [string]$MsbuildPath,
        [string]$ProjectOrSolution,
        [string]$TargetName,
        [string]$ToolsetName,
        [string]$SdkVersion
    )

    $args = @(
        $ProjectOrSolution,
        "/t:$TargetName",
        "/p:Configuration=Release",
        "/p:Platform=Win32",
        "/p:PlatformToolset=$ToolsetName"
    )

    if ($SdkVersion) {
        $args += "/p:WindowsTargetPlatformVersion=$SdkVersion"
    }

    & $MsbuildPath @args
    if ($LASTEXITCODE -ne 0) {
        throw "MSBuild failed for $ProjectOrSolution target $TargetName with exit code $LASTEXITCODE."
    }
}

function Ensure-LtkcLibs {
    param(
        [string]$Root,
        [string]$MsbuildPath,
        [string]$ToolsetName,
        [string]$SdkVersion
    )

    $ltkcRoot = Join-Path $Root "c\src\api\ltkc_win32"
    $lib1 = Join-Path $ltkcRoot "libVS2019\libltkc.lib"
    $lib2 = Join-Path $ltkcRoot "libVS2019\libltkctm.lib"

    if ((Test-Path $lib1) -and (Test-Path $lib2)) {
        return
    }

    Write-Host "Preparing LTKC generated files..."
    Push-Location $ltkcRoot
    try {
        & cmd /c "gencode.bat"
        if ($LASTEXITCODE -ne 0) {
            throw "gencode.bat failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }

    Write-Host "Building LTKC libraries..."
    $libltkcProj = Join-Path $ltkcRoot "projVS2019\libltkc\libltkc.vcxproj"
    $libtmProj = Join-Path $ltkcRoot "projVS2019\libltkcllrporg\libltkcllrporg.vcxproj"

    Invoke-Msbuild -MsbuildPath $MsbuildPath -ProjectOrSolution $libltkcProj -TargetName Build -ToolsetName $ToolsetName -SdkVersion $SdkVersion
    Invoke-Msbuild -MsbuildPath $MsbuildPath -ProjectOrSolution $libtmProj -TargetName Build -ToolsetName $ToolsetName -SdkVersion $SdkVersion

    if (-not ((Test-Path $lib1) -and (Test-Path $lib2))) {
        throw "LTKC libraries were not produced in $ltkcRoot\libVS2019."
    }
}

function Ensure-MercuryApiLib {
    param(
        [string]$Root,
        [string]$MsbuildPath,
        [string]$ToolsetName,
        [string]$SdkVersion
    )

    $libPath = Join-Path $Root "c\projVS2019\MercuryAPI\Release\MercuryAPI.lib"
    if (Test-Path $libPath) {
        return
    }

    Write-Host "Building MercuryAPI library..."
    $mercurySln = Join-Path $Root "c\projVS2019\MercuryAPI\MercuryAPI.sln"
    Invoke-Msbuild -MsbuildPath $MsbuildPath -ProjectOrSolution $mercurySln -TargetName MercuryAPI -ToolsetName $ToolsetName -SdkVersion $SdkVersion

    if (-not (Test-Path $libPath)) {
        throw "MercuryAPI.lib was not produced at $libPath"
    }
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$uriFinal = Resolve-ReaderUri -InUri $Uri -InPort $Port

$readAsyncExe = Resolve-ReadAsyncExe -Root $root -RequestedPath $ReadAsyncPath
$readAsyncSource = Join-Path $root "c\src\samples\readasync.c"
$needBuild = $Rebuild -or (-not $readAsyncExe)

if ((-not $needBuild) -and (Test-Path $readAsyncSource) -and (Test-Path $readAsyncExe)) {
    $srcTime = (Get-Item $readAsyncSource).LastWriteTimeUtc
    $exeTime = (Get-Item $readAsyncExe).LastWriteTimeUtc
    if ($srcTime -gt $exeTime) {
        $needBuild = $true
    }
}

if ($needBuild) {
    if ($NoBuild) {
        throw "Build required but -NoBuild was specified."
    }

    $msbuild = Resolve-Msbuild
    if (-not $msbuild) {
        throw "MSBuild not found. Install Visual Studio Build Tools (C++), then rerun."
    }

    $effectiveToolset = Resolve-PlatformToolset -MsbuildPath $msbuild -RequestedToolset $Toolset
    $effectiveSdkVersion = Resolve-WindowsSdkVersion -RequestedSdkVersion $WindowsSdkVersion

    $readAsyncProj = Join-Path $root "c\projVS2019\Samples\ReadAsync.vcxproj"
    if (-not (Test-Path $readAsyncProj)) {
        throw "ReadAsync project not found at $readAsyncProj"
    }

    Write-Host "[1/2] Building ReadAsync with MSBuild..."
    Write-Host "Using PlatformToolset: $effectiveToolset"
    if ($effectiveSdkVersion) {
        Write-Host "Using Windows SDK:     $effectiveSdkVersion"
    }

    Ensure-LtkcLibs -Root $root -MsbuildPath $msbuild -ToolsetName $effectiveToolset -SdkVersion $effectiveSdkVersion
    Ensure-MercuryApiLib -Root $root -MsbuildPath $msbuild -ToolsetName $effectiveToolset -SdkVersion $effectiveSdkVersion
    Invoke-Msbuild -MsbuildPath $msbuild -ProjectOrSolution $readAsyncProj -TargetName Build -ToolsetName $effectiveToolset -SdkVersion $effectiveSdkVersion

    $readAsyncExe = Resolve-ReadAsyncExe -Root $root -RequestedPath $ReadAsyncPath
    if (-not $readAsyncExe) {
        throw "Build finished but readasync.exe was not found."
    }
}

$pthreadDir = Join-Path $root "c\src\arch\win32\lib"
if (Test-Path $pthreadDir) {
    $env:PATH = "$pthreadDir;$env:PATH"
}

Write-Host "[2/2] Starting live reads..."
Write-Host "Executable: $readAsyncExe"
Write-Host "Reader URI: $uriFinal"
Write-Host "Antenna:    $Antenna"
Write-Host "Read power: $ReadPower cdBm"
Write-Host "Press Ctrl+C to stop.`n"

& $readAsyncExe $uriFinal --ant $Antenna --pow $ReadPower
exit $LASTEXITCODE
