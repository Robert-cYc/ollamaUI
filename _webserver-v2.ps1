param([int]$Port = 8080, [string]$Root = $PSScriptRoot)
$ErrorActionPreference = "Stop"
$mime = @{ ".html"="text/html; charset=utf-8"; ".htm"="text/html; charset=utf-8";
           ".js"="application/javascript; charset=utf-8"; ".css"="text/css; charset=utf-8";
           ".json"="application/json; charset=utf-8"; ".png"="image/png"; ".svg"="image/svg+xml";
           ".ico"="image/x-icon" }
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://localhost:$Port/")
try { $listener.Start() }
catch { Write-Host "Cannot bind port $Port. Try another port or run as admin."; Start-Sleep 8; exit 1 }

# Auto-start hardware telemetry SQLite logger & service
$hwScript = Join-Path $Root "hw_logger.py"
if (Test-Path $hwScript) {
  try {
    Start-Process -FilePath "python" -ArgumentList "`"$hwScript`" --serve" -WindowStyle Hidden
    Write-Host "Hardware SQLite Telemetry service started in background (port 8086)."
  } catch {
    Write-Host "Note: Python hw_logger could not be auto-started: $_"
  }
}

Write-Host "Serving $Root on http://localhost:$Port/  (close this window to stop)"
while ($listener.IsListening) {
  try {
    $ctx = $listener.GetContext()
    $rel = [Uri]::UnescapeDataString($ctx.Request.Url.LocalPath.TrimStart('/'))

    # CORS Preflight
    if ($ctx.Request.HttpMethod -eq "OPTIONS") {
      $ctx.Response.StatusCode = 204
      $ctx.Response.Headers.Add("Access-Control-Allow-Origin", "*")
      $ctx.Response.Headers.Add("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
      $ctx.Response.Headers.Add("Access-Control-Allow-Headers", "Content-Type, Accept, Range")
      $ctx.Response.Close()
      continue
    }

    # Hardware Telemetry API proxy / fallback
    if ($ctx.Request.Url.LocalPath.StartsWith("/api/hardware")) {
      $ctx.Response.Headers.Add("Access-Control-Allow-Origin", "*")
      $handled = $false
      try {
        $targetUrl = "http://127.0.0.1:8086" + $ctx.Request.RawUrl
        $hwReq = [System.Net.HttpWebRequest]::Create($targetUrl)
        $hwReq.Method = $ctx.Request.HttpMethod
        $hwReq.Timeout = 3000
        if ($ctx.Request.HttpMethod -eq "POST" -and $ctx.Request.HasEntityBody) {
          $ctx.Request.InputStream.CopyTo($hwReq.GetRequestStream())
        }
        $hwResp = $hwReq.GetResponse()
        $ctx.Response.ContentType = $hwResp.ContentType
        $ctx.Response.StatusCode = [int]$hwResp.StatusCode
        $hwResp.GetResponseStream().CopyTo($ctx.Response.OutputStream)
        $hwResp.Close()
        $handled = $true
      } catch {
        # Fallback to direct Python CLI if service is starting up
        try {
          if ($ctx.Request.Url.LocalPath -eq "/api/hardware/export") {
            $tempCsv = Join-Path $Root "hardware_export.temp.csv"
            & python (Join-Path $Root "hw_logger.py") --export-csv $tempCsv | Out-Null
            if (Test-Path $tempCsv) {
              $bytes = [IO.File]::ReadAllBytes($tempCsv)
              Remove-Item -Force $tempCsv -ErrorAction SilentlyContinue
              $ctx.Response.ContentType = "text/csv; charset=utf-8"
              $ctx.Response.Headers.Add("Content-Disposition", 'attachment; filename="hardware_telemetry.csv"')
              $ctx.Response.ContentLength64 = $bytes.Length
              $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
              $handled = $true
            }
          } elseif ($ctx.Request.Url.LocalPath -eq "/api/hardware/stats") {
            $outJson = & python (Join-Path $Root "hw_logger.py") --stats-json | Out-String
            $b = [Text.Encoding]::UTF8.GetBytes($outJson)
            $ctx.Response.ContentType = "application/json; charset=utf-8"
            $ctx.Response.ContentLength64 = $b.Length
            $ctx.Response.OutputStream.Write($b, 0, $b.Length)
            $handled = $true
          } elseif ($ctx.Request.Url.LocalPath -eq "/api/hardware/prune") {
            $daysVal = $ctx.Request.QueryString["days"]
            if ([string]::IsNullOrWhiteSpace($daysVal)) { $daysVal = "7" }
            $outJson = & python (Join-Path $Root "hw_logger.py") --prune ([int]$daysVal) | Out-String
            $b = [Text.Encoding]::UTF8.GetBytes('{"status":"ok","message":"' + $outJson.Trim() + '"}')
            $ctx.Response.ContentType = "application/json; charset=utf-8"
            $ctx.Response.ContentLength64 = $b.Length
            $ctx.Response.OutputStream.Write($b, 0, $b.Length)
            $handled = $true
          } elseif ($ctx.Request.HttpMethod -eq "GET") {
            $rangeVal = $ctx.Request.QueryString["range"]
            if ([string]::IsNullOrWhiteSpace($rangeVal)) { $rangeVal = "3600" }
            $maxPts = $ctx.Request.QueryString["maxPoints"]
            if ([string]::IsNullOrWhiteSpace($maxPts)) { $maxPts = "500" }
            $outJson = & python (Join-Path $Root "hw_logger.py") --query ([int]$rangeVal) --max-points ([int]$maxPts) | Out-String
            $b = [Text.Encoding]::UTF8.GetBytes($outJson)
            $ctx.Response.ContentType = "application/json; charset=utf-8"
            $ctx.Response.ContentLength64 = $b.Length
            $ctx.Response.OutputStream.Write($b, 0, $b.Length)
            $handled = $true
          }
        } catch { }
      }
      if (-not $handled) {
        $b = [Text.Encoding]::UTF8.GetBytes('{"status":"unavailable"}')
        $ctx.Response.ContentType = "application/json; charset=utf-8"
        $ctx.Response.ContentLength64 = $b.Length
        $ctx.Response.OutputStream.Write($b, 0, $b.Length)
      }
      $ctx.Response.Close()
      continue
    }

    if ($ctx.Request.Url.LocalPath -eq "/api/run" -and $ctx.Request.HttpMethod -eq "POST") {
      $ctx.Response.Headers.Add("Access-Control-Allow-Origin", "*")
      $reader = New-Object IO.StreamReader($ctx.Request.InputStream, [Text.Encoding]::UTF8)
      $req = $reader.ReadToEnd() | ConvertFrom-Json
      $output = ""
      try {
        if ($req.action -eq "command") { $output = Invoke-Expression $req.command | Out-String }
        elseif ($req.action -eq "read") { $output = Get-Content $req.path -Raw }
        elseif ($req.action -eq "open") { Start-Process $req.path; $output = "Opened $($req.path)" }
        elseif ($req.action -eq "list") {
          $targetPath = if ([string]::IsNullOrWhiteSpace($req.path)) { "." } else { $req.path }
          $depth = if ($req.depth) { [int]$req.depth } else { 1 }
          $items = Get-ChildItem -Path $targetPath -Depth ($depth - 1) -ErrorAction Stop | Select-Object Mode, LastWriteTime, Length, Name
          $output = ($items | Format-Table -AutoSize | Out-String).Trim()
          if ([string]::IsNullOrWhiteSpace($output)) { $output = "(Directory is empty)" }
        }
        elseif ($req.action -eq "git_status") {
          $targetDir = if ([string]::IsNullOrWhiteSpace($req.path)) { $Root } else { $req.path }
          Push-Location $targetDir
          try {
            $branch = (git branch --show-current 2>&1).Trim()
            $status = (git status -s 2>&1).Trim()
            $lastCommit = (git log -1 --oneline 2>&1).Trim()
            $output = "Branch: $branch`nLast Commit: $lastCommit`nChanges:`n" + (if ($status) { $status } else { "(Working tree clean)" })
          } finally {
            Pop-Location
          }
        }
      } catch { $output = "Error: $($_.Exception.Message)" }
      $b = [Text.Encoding]::UTF8.GetBytes($output)
      $ctx.Response.ContentType = "text/plain; charset=utf-8"
      $ctx.Response.ContentLength64 = $b.Length
      $ctx.Response.OutputStream.Write($b, 0, $b.Length)
      $ctx.Response.Close()
      continue
    }
    elseif ([string]::IsNullOrWhiteSpace($rel)) { $rel = "ollama-chat-v2.html" }
    $file = Join-Path $Root $rel
    $full = [IO.Path]::GetFullPath($file)
    if ($full.StartsWith([IO.Path]::GetFullPath($Root)) -and (Test-Path $full -PathType Leaf)) {
      $ext = [IO.Path]::GetExtension($full).ToLower()
      $ctx.Response.ContentType = if ($mime.ContainsKey($ext)) { $mime[$ext] } else { "application/octet-stream" }
      $b = [IO.File]::ReadAllBytes($full)
      $ctx.Response.ContentLength64 = $b.Length
      $ctx.Response.OutputStream.Write($b, 0, $b.Length)
    } else {
      $ctx.Response.StatusCode = 404
      $b = [Text.Encoding]::UTF8.GetBytes("404 not found: $rel")
      $ctx.Response.OutputStream.Write($b, 0, $b.Length)
    }
  } catch { 
    Write-Host "Error processing request: $_"
  } finally {
    if ($ctx -and $ctx.Response) {
      try { $ctx.Response.Close() } catch {}
    }
  }
}
