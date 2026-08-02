$ErrorActionPreference = "Stop"
$InstallerVersion = "0.6.0"
$InstallerFile = "x86qw-installer-$InstallerVersion.zip"
$InstallerSha256 = "266a6c9967541ed0857fbbed2f834c920f89dda6738fb5bc43cd9aae5548580c"
$InstallerUrls = @(
  "https://github.com/x86dx2/x86qw/releases/download/x86qw-installer-$InstallerVersion/$InstallerFile",
  "https://gitlab.com/api/v4/projects/84813414/packages/generic/x86qw-installer/$InstallerVersion/$InstallerFile"
)

$PreviousConsoleOutputEncoding = [Console]::OutputEncoding
$PreviousPowerShellOutputEncoding = $OutputEncoding
$Utf8Encoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8Encoding
$OutputEncoding = $Utf8Encoding
$InstallerExitCode = $null

try {
  $PythonRuntime = $null
  $PythonCandidates = @(
    [pscustomobject]@{ Command = "py"; Arguments = @("-3") },
    [pscustomobject]@{ Command = "python3"; Arguments = @() },
    [pscustomobject]@{ Command = "python"; Arguments = @() }
  )
  foreach ($Candidate in $PythonCandidates) {
    if (-not (Get-Command $Candidate.Command -ErrorAction SilentlyContinue)) {
      continue
    }
    try {
      $ProbeArguments = @($Candidate.Arguments) + @("--version")
      $VersionOutput = (& $Candidate.Command @ProbeArguments 2>&1 | Out-String).Trim()
      $VersionExitCode = $LASTEXITCODE
    } catch {
      continue
    }
    if ($VersionExitCode -ne 0 -or $VersionOutput -notmatch "Python\s+(\d+)\.(\d+)") {
      continue
    }
    $PythonMajor = [int]$Matches[1]
    $PythonMinor = [int]$Matches[2]
    if ($PythonMajor -gt 3 -or ($PythonMajor -eq 3 -and $PythonMinor -ge 10)) {
      $PythonRuntime = $Candidate
      break
    }
  }

  if ($null -eq $PythonRuntime) {
    $PythonError = @(
      "x86QW: Python 3.10 ou mais recente nao foi encontrado.",
      "Instale com: winget install --id Python.Python.3.13 -e",
      "Depois abra um novo PowerShell e execute o instalador novamente.",
      "O alias da Microsoft Store, sozinho, nao e um Python utilizavel."
    ) -join [Environment]::NewLine
    throw $PythonError
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
        Write-Warning "Mirror indisponivel: $Url"
      }
    }
    if (-not $Downloaded) { throw "x86QW: nenhum mirror entregou o instalador." }
    $Actual = (Get-FileHash -Algorithm SHA256 -Path $Archive).Hash.ToLowerInvariant()
    if ($Actual -ne $InstallerSha256) { throw "x86QW: o instalador baixado falhou na verificacao SHA-256." }
    Expand-Archive -Path $Archive -DestinationPath $WorkDir
    $Root = Join-Path $WorkDir "x86qw-installer-$InstallerVersion"
    $InstallerArguments = @($PythonRuntime.Arguments) + @((Join-Path $Root "x86qw.pyz"), "--online-only") + @($args)
    & $PythonRuntime.Command @InstallerArguments
    $InstallerExitCode = $LASTEXITCODE
  } finally {
    Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
  }

} finally {
  [Console]::OutputEncoding = $PreviousConsoleOutputEncoding
  $OutputEncoding = $PreviousPowerShellOutputEncoding
}

if ($null -ne $InstallerExitCode) {
  if ($InstallerExitCode -ne 0) {
    Write-Error "x86QW: o instalador terminou com codigo $InstallerExitCode." -ErrorAction Continue
  }
  $global:LASTEXITCODE = $InstallerExitCode
}
