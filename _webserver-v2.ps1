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
Write-Host "Serving $Root on http://localhost:$Port/  (close this window to stop)"
while ($listener.IsListening) {
  try {
    $ctx = $listener.GetContext()
    $rel = [Uri]::UnescapeDataString($ctx.Request.Url.LocalPath.TrimStart('/'))
    if ($ctx.Request.Url.LocalPath -eq "/api/run" -and $ctx.Request.HttpMethod -eq "POST") {
      $reader = New-Object IO.StreamReader($ctx.Request.InputStream, [Text.Encoding]::UTF8)
      $req = $reader.ReadToEnd() | ConvertFrom-Json
      $output = ""
      try {
        if ($req.action -eq "command") { $output = Invoke-Expression $req.command | Out-String }
        elseif ($req.action -eq "read") { $output = Get-Content $req.path -Raw }
        elseif ($req.action -eq "open") { Start-Process $req.path; $output = "Opened $($req.path)" }
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
