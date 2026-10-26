<#
  Phan "giong doc" cua `npm run dev` (scripts/dev.mjs).

  App DGT bat buoc quyen Administrator (manifest requireAdministrator), nen Automation API cung phai chay
  cung quyen thi moi dieu khien duoc cua so DGT. Script tu xin UAC mot lan roi:
    -Action Start  mo DgtAutoTTSMM.exe neu chua mo (bo qua khi -NoDesktop) va chay scripts\Start-Api.ps1
                   cua app DGT o che do nen, an cua so, log ra LogDir\tts-api.log (bo qua khi -NoApi).
    -Action Stop   dung API bang scripts\Stop-Api.ps1 cua app DGT (tu tu choi khi dang co file chay).
  Cua so Administrator bi an nen thong bao ghi vao LogDir\tts-launch.log (dev.mjs in lai).
  Ma thoat: 0 xong, 1 loi, 3 nguoi dung tu choi UAC.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [Parameter(Mandatory = $true)][string]$LogDir,
    [ValidateRange(1, 65535)][int]$Port = 8787,
    [ValidateSet('Start', 'Stop')][string]$Action = 'Start',
    [switch]$NoDesktop,
    [switch]$NoApi
)
$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
try { $administrator = ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) }
finally { $identity.Dispose() }

if (-not $administrator) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
        '-AppDir', $AppDir, '-LogDir', $LogDir, '-Port', $Port, '-Action', $Action)
    if ($NoDesktop) { $arguments += '-NoDesktop' }
    if ($NoApi) { $arguments += '-NoApi' }
    $line = ($arguments | ForEach-Object { if ("$_" -match '\s') { '"' + $_ + '"' } else { "$_" } }) -join ' '
    try {
        $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList $line -PassThru -Wait
    } catch {
        Write-Host 'Khong duoc cap quyen Administrator (UAC bi huy).'
        exit 3
    }
    exit $elevated.ExitCode
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$launchLog = Join-Path $LogDir 'tts-launch.log'
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Write-Launch([string]$Text) {
    Write-Host $Text
    [System.IO.File]::AppendAllText($launchLog, ((Get-Date).ToString('HH:mm:ss') + ' ' + $Text + "`r`n"), $utf8)
}

try {
    if ($Action -eq 'Stop') {
        & (Join-Path $AppDir 'scripts\Stop-Api.ps1') -Port $Port *>&1 | ForEach-Object { Write-Launch "$_" }
        exit 0
    }

    if (-not $NoDesktop -and -not (Get-Process -Name DgtAutoTTSMM -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath (Join-Path $AppDir 'DgtAutoTTSMM.exe') -WorkingDirectory $AppDir
        Write-Launch 'Da mo app DGT (DgtAutoTTSMM.exe). Dang nhap tren app neu no hoi.'
    }

    if (-not $NoApi) {
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
            Write-Launch "Cong $Port da co server, khong bat them API."
        } else {
            # Console an rieng dat UTF-8 de log cua dotnet doc duoc; dotnet chay chung console nay.
            $startApi = (Join-Path $AppDir 'scripts\Start-Api.ps1') -replace "'", "''"
            $command = "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}; & '$startApi' -Port $Port"
            Start-Process -FilePath 'powershell.exe' `
                -ArgumentList ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + $command + '"') `
                -WorkingDirectory $AppDir -WindowStyle Hidden `
                -RedirectStandardOutput (Join-Path $LogDir 'tts-api.log') `
                -RedirectStandardError (Join-Path $LogDir 'tts-api.err.log')
            Write-Launch "Da bat API DGT o cong $Port (quyen Administrator), dang build + khoi dong."
        }
    }
    exit 0
} catch {
    Write-Launch ('Loi: ' + $_.Exception.Message)
    exit 1
}
