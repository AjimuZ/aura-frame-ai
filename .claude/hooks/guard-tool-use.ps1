$ErrorActionPreference = 'Stop'

# Purpose: Return a structured Claude Code denial without ever approving a tool call.
function Deny-ToolUse {
    param([Parameter(Mandatory = $true)][string]$Reason)

    $response = @{
        hookSpecificOutput = @{
            hookEventName          = 'PreToolUse'
            permissionDecision     = 'deny'
            permissionDecisionReason = $Reason
        }
    }

    [Console]::Out.WriteLine(($response | ConvertTo-Json -Depth 4 -Compress))
    exit 0
}

# Purpose: Decide whether a repository-relative path belongs to protected personal data.
function Test-ProtectedRelativePath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $normalized = $RelativePath.Replace('\', '/').TrimStart('/').ToLowerInvariant()
    $protectedRoots = @(
        'img',
        'data/input_originali',
        'data/working',
        'data/output',
        'data/reports',
        'thumbnails'
    )

    foreach ($protectedRoot in $protectedRoots) {
        if ($normalized -eq $protectedRoot -or $normalized.StartsWith($protectedRoot + '/')) {
            return $true
        }
    }

    return $false
}

try {
    $rawInput = [Console]::In.ReadToEnd()
    $hookInput = $rawInput | ConvertFrom-Json
} catch {
    Deny-ToolUse 'The safety hook could not parse the tool request.'
}

if ([string]::IsNullOrWhiteSpace($env:CLAUDE_PROJECT_DIR)) {
    Deny-ToolUse 'The project root is unavailable, so the safety boundary cannot be verified.'
}

$projectRoot = [System.IO.Path]::GetFullPath($env:CLAUDE_PROJECT_DIR).TrimEnd('\', '/')
$toolName = [string]$hookInput.tool_name

if ($toolName -eq 'Write' -or $toolName -eq 'Edit') {
    $requestedPath = [string]$hookInput.tool_input.file_path
    if ([string]::IsNullOrWhiteSpace($requestedPath)) {
        Deny-ToolUse 'A file write without a verifiable path was blocked.'
    }

    try {
        $targetPath = [System.IO.Path]::GetFullPath($requestedPath)
    } catch {
        Deny-ToolUse 'A file write with an invalid path was blocked.'
    }

    $insideProject = $targetPath.Equals($projectRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $targetPath.StartsWith($projectRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)

    if (-not $insideProject) {
        Deny-ToolUse 'Writes outside the AuraFrame AI repository are blocked.'
    }

    $relativePath = $targetPath.Substring($projectRoot.Length).TrimStart('\', '/')
    if (Test-ProtectedRelativePath $relativePath) {
        Deny-ToolUse 'Personal-photo inputs and generated personal-data directories are immutable to Claude Code.'
    }

    if ([System.IO.Path]::GetFileName($targetPath) -like '.env*') {
        Deny-ToolUse 'Environment and secret files must not be modified by Claude Code.'
    }
}

if ($toolName -eq 'Bash' -or $toolName -eq 'PowerShell') {
    $command = [string]$hookInput.tool_input.command
    if ([string]::IsNullOrWhiteSpace($command)) {
        Deny-ToolUse 'A shell command without inspectable content was blocked.'
    }

    $normalizedCommand = $command.Replace('\', '/').ToLowerInvariant()
    $protectedTokens = @(
        'img/',
        './img',
        '/img/',
        'data/input_originali',
        'data/working',
        'data/output',
        'data/reports',
        'thumbnails/'
    )

    foreach ($token in $protectedTokens) {
        if ($normalizedCommand.Contains($token)) {
            Deny-ToolUse 'Shell access to protected personal-data paths is blocked.'
        }
    }

    $destructivePatterns = @(
        '(?i)\brm\b[^\r\n]*(?:-rf|-fr|--recursive)',
        '(?i)\bremove-item\b[^\r\n]*-recurse',
        '(?i)\brmdir\b[^\r\n]*(?:/s|-recurse)',
        '(?i)\bdel\b[^\r\n]*/s',
        '(?i)\bgit\s+clean\b',
        '(?i)\bgit\s+reset\s+--hard\b',
        '(?i)\bgit\s+checkout\s+--\b',
        '(?i)\bgit\s+restore\b',
        '(?i)\bgit\s+(?:commit|push|tag)\b'
    )

    foreach ($pattern in $destructivePatterns) {
        if ($command -match $pattern) {
            Deny-ToolUse 'Destructive filesystem actions and automatic Git publication/history changes are blocked.'
        }
    }

    $uploadPatterns = @(
        '(?i)\bcurl(?:\.exe)?\b[^\r\n]*(?:--upload-file|-T\b|--form|-F\b|--data|-d\b)',
        '(?i)\binvoke-(?:webrequest|restmethod)\b[^\r\n]*(?:-method\s+(?:post|put|patch)|-infile\b|-body\b)',
        '(?i)\b(?:scp|sftp|ftp)\b',
        '(?i)\brclone\s+(?:copy|sync|move)\b',
        '(?i)\baws\s+s3\s+(?:cp|sync|mv)\b',
        '(?i)\baz\s+storage\s+blob\s+upload\b',
        '(?i)\bgcloud\s+storage\s+cp\b',
        '(?i)\bgh\s+(?:gist\s+create|release\s+upload|pr\s+create)\b'
    )

    foreach ($pattern in $uploadPatterns) {
        if ($command -match $pattern) {
            Deny-ToolUse 'Outbound upload and cloud-write commands are blocked by the privacy policy.'
        }
    }
}

exit 0

