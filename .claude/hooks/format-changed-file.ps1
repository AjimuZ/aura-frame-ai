$ErrorActionPreference = 'Stop'

try {
    $rawInput = [Console]::In.ReadToEnd()
    $hookInput = $rawInput | ConvertFrom-Json

    $requestedPath = [string]$hookInput.tool_input.file_path
    if ([string]::IsNullOrWhiteSpace($requestedPath) -or [string]::IsNullOrWhiteSpace($env:CLAUDE_PROJECT_DIR)) {
        exit 0
    }

    $projectRoot = [System.IO.Path]::GetFullPath($env:CLAUDE_PROJECT_DIR).TrimEnd('\', '/')
    $targetPath = [System.IO.Path]::GetFullPath($requestedPath)
    $insideProject = $targetPath.StartsWith($projectRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $insideProject -or -not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
        exit 0
    }

    $relativePath = $targetPath.Substring($projectRoot.Length).TrimStart('\', '/').Replace('\', '/').ToLowerInvariant()
    $excludedRoots = @('img/', 'data/', 'thumbnails/', 'node_modules/', 'dist/', 'build/', 'out/')
    foreach ($excludedRoot in $excludedRoots) {
        if ($relativePath.StartsWith($excludedRoot)) {
            exit 0
        }
    }

    $extension = [System.IO.Path]::GetExtension($targetPath).ToLowerInvariant()
    if ($extension -notin @('.ts', '.html', '.scss')) {
        exit 0
    }

    $targetFile = Get-Item -LiteralPath $targetPath
    if ($targetFile.Length -gt 1MB) {
        exit 0
    }

    $prettier = Join-Path $projectRoot 'node_modules\.bin\prettier.cmd'
    if (-not (Test-Path -LiteralPath $prettier -PathType Leaf)) {
        exit 0
    }

    & $prettier '--write' '--' $targetPath *> $null
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine('Local Prettier failed for the edited source file.')
        exit 1
    }
} catch {
    [Console]::Error.WriteLine('The optional local formatter hook failed safely.')
    exit 1
}

exit 0

