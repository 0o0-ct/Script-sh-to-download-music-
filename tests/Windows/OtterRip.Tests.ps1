# OtterRip Windows Batch Tests — Pester
#
# Basic tests that verify the Windows batch file exists, has the correct
# version string, and can extract the embedded PowerShell script.

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$BatFile = Join-Path $ProjectRoot "download-music_v7-windows.bat"

Describe "OtterRip Windows batch" {

    It "batch file exists" {
        Test-Path $BatFile | Should -BeTrue
    }

    It "contains the expected version string" {
        $content = Get-Content $BatFile -Raw
        $content | Should -Match "v7\.9\.0"
    }

    It "contains the PowerShell marker" {
        $content = Get-Content $BatFile -Raw
        $content | Should -Match "__OTTERRIP_PS1__"
    }

    It "can extract the embedded PowerShell script" {
        $tempPs1 = Join-Path $env:TEMP "otterrip_test_extract.ps1"
        try {
            # The batch extracts to a .ps1 temp file; simulate extraction
            $content = Get-Content $BatFile -Raw
            $marker = "#__OTTERRIP_PS1__"
            if ($content -match "$marker\r?\n(.*)"ms) {
                $psContent = $Matches[1]
                Set-Content -Path $tempPs1 -Value $psContent -Encoding UTF8
                Test-Path $tempPs1 | Should -BeTrue
                (Get-Content $tempPs1 -Raw).Length | Should -BeGreaterThan 100
            }
            else {
                Fail "PowerShell marker not found in batch file"
            }
        }
        finally {
            if (Test-Path $tempPs1) { Remove-Item $tempPs1 -Force }
        }
    }

    It "batch file is executable" {
        $info = Get-Item $BatFile
        $info.Extension | Should -Be ".bat"
        $info.Length | Should -BeGreaterThan 0
    }
}

Describe "OtterRip py_core bridge" {

    It "py_core module path exists" {
        $coreDir = Join-Path $ProjectRoot "lib"
        Test-Path $coreDir | Should -BeTrue
    }

    It "VERSION file exists and contains a version" {
        $versionFile = Join-Path $ProjectRoot "lib" "otterrip_core" "VERSION"
        if (Test-Path $versionFile) {
            $version = Get-Content $versionFile -Raw
            $version | Should -Match "\d+\.\d+\.\d+"
        }
        else {
            # Skip if running without full checkout
            Set-ItResult -Skipped -Because "VERSION file not found"
        }
    }
}
