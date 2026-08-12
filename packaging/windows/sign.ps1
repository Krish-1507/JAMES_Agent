# Windows Authenticode signing for JAMES release artifacts.
#
# Requires the WIN_CERTIFICATE_BASE64 (pfx/p12) and WIN_CERTIFICATE_PASSWORD
# environment variables. Signs the PyInstaller EXE and/or the Inno Setup
# installer, then timestamps with the public DigiCert timestamp server.
#
#   .\sign.ps1                        # signs dist\JAMES.exe
#   .\sign.ps1 -Artifacts "dist/JAMES-Setup-*.exe"
#
# Works out of the box on GitHub Actions windows-latest runners.

param(
    [string]$Artifacts = "dist\JAMES.exe"
)

$ErrorActionPreference = "Stop"

if (-not $env:WIN_CERTIFICATE_BASE64) {
    Write-Error "WIN_CERTIFICATE_BASE64 is not set - nothing to sign with."
    exit 1
}

$certPath = Join-Path $env:RUNNER_TEMP "james_cert.pfx"
[IO.File]::WriteAllBytes($certPath, [Convert]::FromBase64String($env:WIN_CERTIFICATE_BASE64))
$password = ConvertTo-SecureString $env:WIN_CERTIFICATE_PASSWORD -AsPlainText -Force

Import-PfxCertificate -FilePath $certPath -CertStoreLocation Cert:\CurrentUser\My `
    -Password $password | Out-Null

$files = Get-ChildItem -Path $Artifacts -ErrorAction SilentlyContinue
if (-not $files) {
    Write-Error "No artifacts matched '$Artifacts'."
    exit 1
}

foreach ($file in $files) {
    & signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
        /sm /s My /n "JAMES Contributors" $file.FullName
    if ($LASTEXITCODE -ne 0) {
        Write-Error "signtool failed for $($file.FullName)"
        exit 1
    }
    Write-Output "[+] Signed $($file.FullName)"
}
