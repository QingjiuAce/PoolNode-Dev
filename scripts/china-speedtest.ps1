# =====================================================================
# Pool PoC V0.2 - Mainland China direct-connection speed test (PowerShell)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File china-speedtest.ps1 `
#       -Url https://dl.bszx.site/submissions/originals/pool-public-smoke-test.jpg `
#       -Isp mobile -Rounds 3
#
#   -Isp  : unicom | telecom | mobile | broadcast | other   (choose MANUALLY,
#           never guess by IP)
#   -Rounds: short downloads per run (default 3)
#
# Output:
#   reports/network-tests/<date>/china-<isp>.json   (machine-readable)
#   reports/network-tests/<date>/summary.md         (human-readable, NOT TESTED
#                                                    placeholders for others)
#
# Requires: Windows 10+ (built-in curl.exe). No admin rights needed.
# Direct connection only: if a proxy is detected the script WARNS and stops,
# because proxied results would not reflect the real mainland route.
# =====================================================================
param(
    [string]$Url = "https://dl.bszx.site/submissions/originals/pool-public-smoke-test.jpg",
    [ValidateSet("unicom", "telecom", "mobile", "broadcast", "other")]
    [string]$Isp = "other",
    [int]$Rounds = 3,
    # curl.exe does NOT honor the Windows system (WinINET) proxy - only env
    # vars and .curlrc. With this switch, if both are clean, the WinINET
    # warning is skipped and the run proceeds; the presence is still recorded
    # in the report (wininet_proxy_present=true, bypassed_by=curl.exe).
    [switch]$SkipWinInetCheck
)

$ErrorActionPreference = "Continue"
$Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:sszzz"
$Host_ = ([System.Uri]$Url).Host

Write-Host "=== Pool China Direct Speed Test ==="
Write-Host "URL  : $Url"
Write-Host "ISP  : $Isp  (manually selected)"
Write-Host "Time : $Stamp"

# ---- proxy detection (warn & abort: results would be meaningless) ----
$proxyVars = @($env:HTTP_PROXY, $env:HTTPS_PROXY, $env:ALL_PROXY, $env:http_proxy, $env:https_proxy, $env:all_proxy) | Where-Object { $_ }
$wininet = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction SilentlyContinue
$wininetProxyOn = ($wininet.ProxyEnable -eq 1 -and $wininet.ProxyServer)
if ($wininetProxyOn) { $proxyVars += "WinINET system proxy: $($wininet.ProxyServer)" }
if ($proxyVars.Count -gt 0) {
    $curlrc = Join-Path $env:USERPROFILE ".curlrc"
    $curlrcClean = -not (Test-Path $curlrc)
    if ($SkipWinInetCheck -and $curlrcClean -and (@($env:HTTP_PROXY, $env:HTTPS_PROXY, $env:ALL_PROXY, $env:http_proxy, $env:https_proxy, $env:all_proxy) | Where-Object { $_ }).Count -eq 0) {
        Write-Host "[NOTE] WinINET system proxy present ($($wininet.ProxyServer)) but curl.exe bypasses it (no env proxy, no .curlrc). Continuing DIRECT; recorded in report." -ForegroundColor Yellow
        $wininetBypassed = $true
    } else {
        Write-Host ""
        Write-Host "[ABORT] Proxy detected - the test must be DIRECT (no proxy):" -ForegroundColor Red
        $proxyVars | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        if (-not $curlrcClean) { Write-Host "  .curlrc found: $curlrc" -ForegroundColor Red }
        Write-Host "Disable proxy (env vars / system proxy / VPN client) and re-run." -ForegroundColor Red
        exit 2
    }
} else {
    $wininetBypassed = $false
    Write-Host "[OK] No proxy detected - testing direct connection."
}

# ---- curl.exe check ----
$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if (-not $curl) {
    Write-Host "[ABORT] curl.exe not found (Windows 10 1803+ has it built-in)." -ForegroundColor Red
    exit 2
}

# ---- DNS ----
Write-Host "`n--- DNS resolve ($Host_) ---"
$dnsIps = @()
try {
    $answers = Resolve-DnsName $Host_ -Type A -ErrorAction Stop | Where-Object { $_.IPAddress }
    $dnsIps = $answers | ForEach-Object { $_.IPAddress }
    $dnsIps | ForEach-Object { Write-Host "  A -> $_" }
} catch {
    Write-Host "  [WARN] DNS resolve failed: $($_.Exception.Message)" -ForegroundColor Yellow
}
$dnsOk = ($dnsIps.Count -gt 0)

# ---- ping (4 packets) ----
Write-Host "`n--- ping $Host_ x4 ---"
$ping = @{ sent = 0; received = 0; avgMs = $null }
try {
    $replies = Test-Connection -ComputerName $Host_ -Count 4 -ErrorAction SilentlyContinue
    $ping.sent = 4
    $ping.received = @($replies).Count
    if ($ping.received -gt 0) {
        $times = @($replies | ForEach-Object { if ($_.ResponseTime) { [double]$_.ResponseTime } })
        if ($times.Count -gt 0) { $ping.avgMs = [math]::Round(($times | Measure-Object -Average).Average, 1) }
    }
} catch { }
Write-Host ("  sent={0} received={1} avg={2}ms" -f $ping.sent, $ping.received, $(if ($ping.avgMs) { $ping.avgMs } else { "N/A" }))

# ---- download rounds ----
$fmt = 'code=%{http_code} dns=%{time_namelookup} tcp=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total} speed=%{speed_download} size=%{size_download}'
$roundResults = New-Object System.Collections.Generic.List[object]
$tmpFile = Join-Path $env:TEMP ("pool-dl-" + [guid]::NewGuid().ToString() + ".bin")

for ($i = 1; $i -le $Rounds; $i++) {
    Write-Host "`n--- download round $i/$Rounds ---"
    $out = [string](& curl.exe -sS -o $tmpFile -w $fmt --max-time 120 $Url 2>&1 | Select-Object -Last 1)
    $rc = $LASTEXITCODE
    $round = [ordered]@{ round = $i; status = "ok"; http_code = $null; dns_s = $null; tcp_s = $null; tls_s = $null; ttfb_s = $null; total_s = $null; speed_Bps = $null; size_bytes = $null; sha256 = $null; error = $null }
    if ($rc -ne 0 -or $out -notmatch 'code=200') {
        $round.status = "failed"
        $round.error = "curl exit=$rc output=$out"
        Write-Host "  [FAIL] $out" -ForegroundColor Red
    } else {
        # curl -w field name -> report field name mapping
        $map = @{ code = "http_code"; dns = "dns_s"; tcp = "tcp_s"; tls = "tls_s"; ttfb = "ttfb_s"; total = "total_s"; speed = "speed_Bps"; size = "size_bytes" }
        foreach ($kv in ($out -split '\s+')) {
            $p = $kv -split '=', 2
            if ($p.Count -eq 2 -and $map.ContainsKey($p[0])) { $round[$map[$p[0]]] = $p[1] }
        }
        $hash = (Get-FileHash -Path $tmpFile -Algorithm SHA256).Hash.ToLower()
        $round.sha256 = $hash
        $mbps = 0
        if ($round.speed_Bps) { $mbps = [math]::Round([double]$round.speed_Bps * 8 / 1e6, 1) }
        Write-Host ("  HTTP {0}  size={1}B  total={2}s  speed={3} MB/s ({4} Mbps)" -f $round.http_code, $round.size_bytes, $round.total_s, [math]::Round([double]$round.speed_Bps / 1e6, 2), $mbps)
        Write-Host ("  sha256: {0}" -f $hash)
    }
    $roundResults.Add($round) | Out-Null
}
Remove-Item $tmpFile -ErrorAction SilentlyContinue

# ---- report ----
$reportDir = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\reports\network-tests\$(Get-Date -Format 'yyyy-MM-dd')"
$reportDir = [System.IO.Path]::GetFullPath($reportDir)
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

$record = [ordered]@{
    test_time   = $Stamp
    isp         = $Isp
    isp_source  = "manually selected"
    url         = $Url
    host        = $Host_
    dns_ok      = $dnsOk
    dns_a       = $dnsIps
    ping        = $ping
    rounds      = $roundResults
    wininet_proxy_present = [bool]$wininetProxyOn
    wininet_bypassed_by_curl = [bool]$wininetBypassed
    notes       = "direct connection (curl.exe); proxy checked before run"
}
$jsonPath = Join-Path $reportDir "china-$Isp.json"
$record | ConvertTo-Json -Depth 4 | Out-File -FilePath $jsonPath -Encoding utf8
Write-Host "`n[OK] JSON report: $jsonPath" -ForegroundColor Green

# ---- summary.md (all four ISPs; missing ones = NOT TESTED) ----
$ispLabels = @{ unicom = "China Unicom (China Unicom)"; telecom = "China Telecom (China Telecom)"; mobile = "China Mobile (China Mobile)"; broadcast = "China Broadcast (China Broadcast)"; other = "Other (Other)" }
$md = New-Object System.Text.StringBuilder
[void]$md.AppendLine("# Pool mainland network test summary ($(Get-Date -Format 'yyyy-MM-dd'))")
[void]$md.AppendLine("")
[void]$md.AppendLine("| ISP | Test time | DNS | ping loss/avg | HTTP | avg speed (Mbps) | SHA256 consistent | Status |")
[void]$md.AppendLine("| --- | --- | --- | --- | --- | --- | --- | --- |")
foreach ($isp in @("unicom", "telecom", "mobile", "broadcast")) {
    $jp = Join-Path $reportDir "china-$isp.json"
    if (Test-Path $jp) {
        try {
            $j = Get-Content $jp -Raw | ConvertFrom-Json
            $ok = ($j.rounds | Where-Object { $_.status -eq "ok" }).Count
            $speeds = @($j.rounds | Where-Object { $_.speed_Bps } | ForEach-Object { [double]$_.speed_Bps * 8 / 1e6 })
            $avg = if ($speeds.Count -gt 0) { [math]::Round(($speeds | Measure-Object -Average).Average, 1) } else { "N/A" }
            $loss = if ($j.ping.sent -gt 0) { "{0}/{1}ms" -f ($j.ping.sent - $j.ping.received), $(if ($j.ping.avgMs) { $j.ping.avgMs } else { "?" }) } else { "N/A" }
            $code = $j.rounds[0].http_code
            [void]$md.AppendLine("| $($ispLabels[$isp]) | $($j.test_time) | ok | $loss | $code | $avg | see json | TESTED |")
        } catch {
            [void]$md.AppendLine("| $($ispLabels[$isp]) | parse error | - | - | - | - | - | ERROR |")
        }
    } else {
        [void]$md.AppendLine("| $($ispLabels[$isp]) | - | - | - | - | - | - | **NOT TESTED** |")
    }
}
[void]$md.AppendLine("")
[void]$md.AppendLine("_Evening peak runs (20:00-23:00 CST) are separate entries in the same folder._")
$mdPath = Join-Path $reportDir "summary.md"
$md.ToString() | Out-File -FilePath $mdPath -Encoding utf8
Write-Host "[OK] Summary   : $mdPath" -ForegroundColor Green
Write-Host "`nDone. Send the JSON + summary.md back with your network info (ISP/city/access type)."
