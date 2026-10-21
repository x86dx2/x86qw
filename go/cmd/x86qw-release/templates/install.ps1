& {
# Every argument is forwarded to `x86qw setup`; a typed param block would
# bind only the first one.
$BootstrapArguments = @($args)
# Windows PowerShell 5.1 reads BOM-less scripts as ANSI: keep this file ASCII.
# x86QW bootstrap: downloads the native x86QW CLI pinned by size and SHA-256
# and runs `x86qw setup`. No Python required.
$ErrorActionPreference = "Stop"
$Version = "{{.Version}}"
$File = "x86qw-$Version-windows-amd64.exe"
$Sha256 = "{{index .SHA256 "windows-amd64"}}"
$Size = {{index .Size "windows-amd64"}}
$Url = "https://github.com/x86dx2/x86qw/releases/download/x86qw-cli-$Version/$File"
if ([Environment]::Is64BitOperatingSystem -ne $true) { throw "x86QW: 64-bit Windows is required." }
$Work = Join-Path ([IO.Path]::GetTempPath()) ("x86qw-bootstrap-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Work | Out-Null
$Binary = Join-Path $Work "x86qw.exe"
try {
  Write-Host "- Downloading x86QW $Version ($File)"
  Add-Type -AssemblyName System.Net.Http
  $Client = [System.Net.Http.HttpClient]::new()
  $Client.Timeout = [TimeSpan]::FromSeconds(300)
  try {
    $Bytes = $Client.GetByteArrayAsync($Url).GetAwaiter().GetResult()
  } finally { $Client.Dispose() }
  if ($Bytes.Length -ne $Size) { throw "x86QW: unexpected size ($($Bytes.Length), expected $Size); nothing was installed." }
  [IO.File]::WriteAllBytes($Binary, $Bytes)
  $Actual = (Get-FileHash -Algorithm SHA256 -Path $Binary).Hash.ToLowerInvariant()
  if ($Actual -ne $Sha256) { throw "x86QW: SHA-256 mismatch; nothing was installed." }
  Write-Host "OK Download verified"
  & $Binary setup @BootstrapArguments
  $global:LASTEXITCODE = $LASTEXITCODE
  # Setup puts x86qw on the user PATH, which new terminals see; when this
  # script runs in the user's own session (irm ... | iex), it sees it too.
  foreach ($Entry in ([Environment]::GetEnvironmentVariable("Path", "User") -split ";")) {
    if ($Entry -and (($env:Path -split ";") -notcontains $Entry)) { $env:Path += ";$Entry" }
  }
} finally {
  Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
}
} @args
