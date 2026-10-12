<#
.SYNOPSIS
  Run a live Lighthouse audit on the worker VM and open the HTML report (saved to our private S3 bucket) in Chrome.

.EXAMPLE
  .\scripts\lighthouse-demo.ps1 -Url https://example.com
  .\scripts\lighthouse-demo.ps1 -Url https://example.com -Owner demo

.NOTES
  Set LIGHTHOUSE_SSH_HOST (user@host of the worker VM) or pass -SshHost.
  Needs a passwordless SSH key (default $HOME\.ssh\claude_key) installed on the VM.
  The worker listens on 127.0.0.1:3000 on the VM only, so this opens a temporary SSH tunnel
  and closes it again when done. Contains no AWS keys: the worker uploads to S3 with its own
  write-only key and returns a presigned (expiring) link.
#>
param(
  [Parameter(Mandatory = $true)][string]$Url,
  [string]$Owner = "demo",
  [string]$SshHost = $env:LIGHTHOUSE_SSH_HOST,
  [string]$SshKey = "$HOME\.ssh\claude_key",
  [int]$LocalPort = 3000,
  [string]$Chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
)

$ErrorActionPreference = "Stop"
if (-not $SshHost) { throw "Set `$env:LIGHTHOUSE_SSH_HOST (user@host of the worker VM) or pass -SshHost." }
$base = "http://127.0.0.1:$LocalPort"
$tunnel = $null

function Test-Worker {
  try { (Invoke-RestMethod "$base/health" -TimeoutSec 2).status -eq "ok" } catch { $false }
}

try {
  if (-not (Test-Worker)) {
    Write-Host "Opening SSH tunnel to the worker..."
    $tunnel = Start-Process ssh -WindowStyle Hidden -PassThru -ArgumentList @(
      "-i", $SshKey, "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
      "-N", "-L", "${LocalPort}:127.0.0.1:3000", $SshHost)
    $up = $false
    for ($i = 0; $i -lt 15 -and -not $up; $i++) { Start-Sleep -Seconds 1; $up = Test-Worker }
    if (-not $up) { throw "Worker not reachable through the tunnel (check SSH key and that the container is running)." }
  }

  Write-Host "Auditing $Url (about 20-40 seconds)..."
  $body = @{ url = $Url; owner = $Owner } | ConvertTo-Json
  $r = Invoke-RestMethod -Method Post "$base/audit" -ContentType "application/json" -Body $body -TimeoutSec 180

  if (-not $r.load_success) { throw "Audit failed: $($r.error)" }
  if ($r.reports.report_error) { throw "Audit ran but saving to S3 failed: $($r.reports.report_error)" }
  if (-not $r.reports.html_preview_url) { throw "No report link returned (is LIGHTHOUSE_REPORTS_BUCKET set on the worker?)" }

  Write-Host ("Scores: perf {0} | a11y {1} | best-practices {2} | seo {3}" -f $r.scores.performance, $r.scores.accessibility, $r.scores.best_practices, $r.scores.seo)
  Write-Host "Saved to S3 key: $($r.reports.html_s3_key)"
  Write-Host "Link expires in $([math]::Round($r.reports.report_url_expires_in_sec / 86400, 1)) day(s)."

  $link = $r.reports.html_preview_url
  if (Test-Path $Chrome) { Start-Process $Chrome "`"$link`"" } else { Start-Process $link }
}
finally {
  if ($tunnel -and -not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force }
}
