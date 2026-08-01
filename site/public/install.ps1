$ErrorActionPreference = "Stop"
$InstallerVersion = "0.5.0"
$InstallerFile = "x86qw-installer-$InstallerVersion.zip"
$InstallerSha256 = "79ce5e2dcfdf2e3f0c3d03c79c34b7b4ee6ec28408ff6c298e8b7b8159ab352a"
$InstallerUrls = @(
  "https://github.com/x86dx2/x86qw/releases/download/x86qw-installer-$InstallerVersion/$InstallerFile",
  "https://gitlab.com/api/v4/projects/84813414/packages/generic/x86qw-installer/$InstallerVersion/$InstallerFile"
)

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "x86QW: Python 3 é necessário para executar o instalador."
}

$WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("x86qw-installer-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $WorkDir | Out-Null
$InstallerExitCode = $null
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
  $InstallerExitCode = $LASTEXITCODE
} finally {
  Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
}

if ($null -ne $InstallerExitCode) {
  $global:LASTEXITCODE = $InstallerExitCode
  if ($InstallerExitCode -ne 0) {
    Write-Error "x86QW: o instalador terminou com código $InstallerExitCode." -ErrorAction Continue
  }
}
