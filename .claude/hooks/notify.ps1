$ErrorActionPreference = 'Stop'

try {
    $rawInput = [Console]::In.ReadToEnd()
    $hookInput = $rawInput | ConvertFrom-Json
    $notificationType = [string]$hookInput.notification_type

    if ($notificationType -eq 'permission_prompt') {
        $message = 'Claude Code needs permission to continue.'
    } elseif ($notificationType -eq 'idle_prompt') {
        $message = 'Claude Code is waiting for your input.'
    } else {
        exit 0
    }

    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $notification = New-Object System.Windows.Forms.NotifyIcon
    $notification.Icon = [System.Drawing.SystemIcons]::Information
    $notification.Visible = $true
    $notification.BalloonTipTitle = 'AuraFrame AI - Claude Code'
    $notification.BalloonTipText = $message
    $notification.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
    $notification.ShowBalloonTip(3000)
    Start-Sleep -Milliseconds 1200
    $notification.Dispose()
} catch {
    try {
        [Console]::Beep(880, 150)
    } catch {
        # Best-effort notification failures must never interrupt Claude Code.
    }
}

exit 0

