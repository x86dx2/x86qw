$ErrorActionPreference = "Stop"
$InstallerVersion = "0.1.0"
$InstallerFile = "x86qw-installer-$InstallerVersion.zip"
$InstallerSha256 = "9e9ca4cd531b9f71194ca943a9f284a56768d8c9e2d0c3308b85cab8702f330a"
$InstallerUrls = @(
  "https://github.com/x86dx2/x86qw/releases/download/x86qw-installer-$InstallerVersion/$InstallerFile",
  "https://gitlab.com/api/v4/projects/84813414/packages/generic/x86qw-installer/$InstallerVersion/$InstallerFile"
)

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "x86QW: Python 3 é necessário para executar o instalador."
}

$WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("x86qw-installer-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $WorkDir | Out-Null
try {
  $Archive = Join-Path $WorkDir $InstallerFile
  $Downloaded = $false
  foreach ($Url in $InstallerUrls) {
    try {
      Write-Host "x86QW: baixando instalador $InstallerVersion..."
      Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Archive
      $Downloaded = $true
      break
    } catch {
      Write-Warning "Mirror indisponível: $Url"
    }
  }
  if (-not $Downloaded) { throw "x86QW: nenhum mirror entregou o instalador." }
  $Actual = (Get-FileHash -Algorithm SHA256 -Path $Archive).Hash.ToLowerInvariant()
  if ($Actual -ne $InstallerSha256) { throw "x86QW: o instalador baixado falhou na verificação SHA-256." }
  Expand-Archive -Path $Archive -DestinationPath $WorkDir
  $Root = Join-Path $WorkDir "x86qw-installer-$InstallerVersion"
  & python (Join-Path $Root "x86qw.pyz") --online-only @args
  exit $LASTEXITCODE
} finally {
  Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
}
