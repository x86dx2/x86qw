& {
param([object[]]$BootstrapArguments)
# x86QW bootstrap: downloads the native x86QW CLI pinned by size and SHA-256
# and runs `x86qw setup`. No Python required.
$ErrorActionPreference = "Stop"
$Version = "2.0.0"
$File = "x86qw-$Version-windows-amd64.exe"
$Sha256 = "d49da69816a6a9d6d97043ed60d45a2ff7a559bcf63c1268e2f327b9f26acff3"
$Size = 13570560
$Url = "https://github.com/x86dx2/x86qw/releases/download/x86qw-cli-$Version/$File"
if ([Environment]::Is64BitOperatingSystem -ne $true) { throw "x86QW: Windows 64-bit é necessário." }
$Work = Join-Path ([IO.Path]::GetTempPath()) ("x86qw-bootstrap-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Work | Out-Null
$Binary = Join-Path $Work "x86qw.exe"
try {
  Write-Host "· Baixando x86QW $Version ($File)"
  Add-Type -AssemblyName System.Net.Http
  $Client = [System.Net.Http.HttpClient]::new()
  $Client.Timeout = [TimeSpan]::FromSeconds(300)
  try {
    $Bytes = $Client.GetByteArrayAsync($Url).GetAwaiter().GetResult()
  } finally { $Client.Dispose() }
  if ($Bytes.Length -ne $Size) { throw "x86QW: tamanho inesperado ($($Bytes.Length), esperado $Size); nada foi instalado." }
  [IO.File]::WriteAllBytes($Binary, $Bytes)
  $Actual = (Get-FileHash -Algorithm SHA256 -Path $Binary).Hash.ToLowerInvariant()
  if ($Actual -ne $Sha256) { throw "x86QW: SHA-256 divergente; nada foi instalado." }
  Write-Host "✓ Download verificado"
  & $Binary setup @BootstrapArguments
  $global:LASTEXITCODE = $LASTEXITCODE
} finally {
  Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
}
} @args
