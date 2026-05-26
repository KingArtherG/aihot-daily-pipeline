param(
  [string]$RadarDir = "$env:USERPROFILE\ai-news-radar",
  [int]$WindowHours = 24,
  [int]$RadarTake = 80
)

$ErrorActionPreference = "Stop"

$repoDir = Split-Path -Parent $PSScriptRoot
$radarPython = Join-Path $RadarDir ".venv\Scripts\python.exe"
$radarData = Join-Path $RadarDir "data\latest-24h.json"
$defaultSiteTitle = [System.Text.Encoding]::UTF8.GetString(
  [System.Convert]::FromBase64String("S2luZyBBSSDml6nmiqU=")
)

if (-not (Test-Path $radarPython)) {
  throw "Radar Python not found: $radarPython"
}

Push-Location $RadarDir
try {
  & $radarPython "scripts\update_news.py" "--output-dir" "data" "--window-hours" "$WindowHours"
}
finally {
  Pop-Location
}

$env:SITE_TITLE = if ($env:SITE_TITLE) { $env:SITE_TITLE } else { $defaultSiteTitle }
$env:AUTHOR_NAME = if ($env:AUTHOR_NAME) { $env:AUTHOR_NAME } else { "KingArtherG" }
$env:BASE_URL = if ($env:BASE_URL) { $env:BASE_URL } else { "https://KingArtherG.github.io/aihot-daily-pipeline/" }
$env:AIHOT_SOURCE = "hybrid"
$env:AIHOT_HOURS = "$WindowHours"
$env:AIHOT_TAKE = if ($env:AIHOT_TAKE) { $env:AIHOT_TAKE } else { "30" }
$env:RADAR_URL = $radarData
$env:RADAR_TAKE = "$RadarTake"
$env:ENRICH_WITH_LLM = if ($env:ENRICH_WITH_LLM) { $env:ENRICH_WITH_LLM } else { "auto" }

Push-Location $repoDir
try {
  & $radarPython "scripts\build_aihot_daily.py"
}
finally {
  Pop-Location
}
