$ErrorActionPreference = 'Stop'
$script:candidatePaths = @()

# Purpose: Add a repository path to the audit set only when it is a monitored configuration file.
function Add-MonitoredPath {
    param(
        [AllowEmptyString()][string]$Path,
        [Parameter(Mandatory = $true)][string]$ProjectRoot
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }

    try {
        $absolutePath = if ([System.IO.Path]::IsPathRooted($Path)) {
            [System.IO.Path]::GetFullPath($Path)
        } else {
            [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Path))
        }
    } catch {
        return
    }

    $insideProject = $absolutePath.Equals($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $absolutePath.StartsWith($ProjectRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $insideProject) {
        return
    }

    $relativePath = $absolutePath.Substring($ProjectRoot.Length).TrimStart('\', '/').Replace('\', '/')
    $monitoredPaths = @(
        'CLAUDE.md',
        '.claude/settings.json',
        'package.json',
        'engine/config.yaml',
        '.gitignore'
    )

    if ($monitoredPaths -contains $relativePath) {
        $script:candidatePaths += $relativePath
    }
}

try {
    if ([string]::IsNullOrWhiteSpace($env:CLAUDE_PROJECT_DIR)) {
        exit 0
    }

    $projectRoot = [System.IO.Path]::GetFullPath($env:CLAUDE_PROJECT_DIR).TrimEnd('\', '/')
    $rawInput = [Console]::In.ReadToEnd()
    $hookInput = $rawInput | ConvertFrom-Json

    Add-MonitoredPath -Path ([string]$hookInput.tool_input.file_path) -ProjectRoot $projectRoot
    Add-MonitoredPath -Path ([string]$hookInput.file_path) -ProjectRoot $projectRoot

    if ([string]$hookInput.hook_event_name -eq 'ConfigChange' -and [string]$hookInput.source -eq 'project_settings') {
        Add-MonitoredPath -Path '.claude/settings.json' -ProjectRoot $projectRoot
    }

    if ([string]$hookInput.tool_name -eq 'Bash' -or [string]$hookInput.tool_name -eq 'PowerShell') {
        $statusLines = & git -C $projectRoot status --porcelain --untracked-files=all 2>$null
        foreach ($statusLine in $statusLines) {
            if ($statusLine.Length -lt 4) {
                continue
            }

            $statusPath = $statusLine.Substring(3).Trim().Trim('"')
            if ($statusPath.Contains(' -> ')) {
                $statusPath = ($statusPath -split ' -> ')[-1].Trim('"')
            }
            Add-MonitoredPath -Path $statusPath -ProjectRoot $projectRoot
        }
    }

    $uniquePaths = $script:candidatePaths | Sort-Object -Unique
    if ($uniquePaths.Count -eq 0) {
        exit 0
    }

    $logDirectory = Join-Path $projectRoot '.claude\logs'
    $logPath = Join-Path $logDirectory 'audit.jsonl'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

    foreach ($relativePath in $uniquePaths) {
        $record = @{
            timestampUtc = [DateTime]::UtcNow.ToString('o')
            event        = [string]$hookInput.hook_event_name
            tool         = [string]$hookInput.tool_name
            source       = [string]$hookInput.source
            path         = $relativePath
        }
        ($record | ConvertTo-Json -Compress) | Add-Content -LiteralPath $logPath -Encoding UTF8
    }
} catch {
    # Audit is deliberately non-blocking and never emits private input data.
    exit 0
}

exit 0
