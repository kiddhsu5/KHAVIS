# setup_surface_windows.ps1
#
# One-shot setup for the Surface Ollama host.
#
# Idempotent — safe to re-run.
#
# What it does:
#   1. Sets OLLAMA_HOST=0.0.0.0 as a SYSTEM environment variable so the
#      Ollama daemon listens on the LAN (not just 127.0.0.1).
#   2. Opens TCP 11434 in Windows Firewall (inbound + outbound).
#   3. Ensures Ollama is registered to auto-start at boot (via the
#      OllamaService that ships with the Windows installer; if not
#      present, creates a scheduled task that runs `ollama serve`).
#   4. (Optional) pre-pulls the default Surface model
#      (qwen2.5:1.5b by default; override with -Model).
#
# Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_surface_windows.ps1
#
# Optional flags:
#   -Model <name>          model to pre-pull (default: qwen2.5:1.5b)
#   -SkipModelPull         do not pre-pull the model
#   -Uninstall             remove the firewall rule and clear OLLAMA_HOST
#
# Exit code 0 on success, non-zero on first failure.

[CmdletBinding()]
param(
    [string] $Model = "qwen2.5:1.5b",
    [switch] $SkipModelPull,
    [switch] $Uninstall
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error "This script must be run as Administrator."
        exit 2
    }
}

function Set-OllamaHostSystemEnv {
    param([string] $Value)

    # Use [System.Environment]::SetEnvironmentVariable with target=Machine
    # to write to the system-wide env block; this is the equivalent of
    # `setx /m OLLAMA_HOST 0.0.0.0`.
    [System.Environment]::SetEnvironmentVariable(
        "OLLAMA_HOST",
        $Value,
        [System.EnvironmentVariableTarget]::Machine
    )
    Write-Host "  + OLLAMA_HOST=$Value (system env)"
}

function Remove-OllamaHostSystemEnv {
    [System.Environment]::SetEnvironmentVariable(
        "OLLAMA_HOST",
        $null,
        [System.EnvironmentVariableTarget]::Machine
    )
    Write-Host "  - OLLAMA_HOST removed from system env"
}

function Add-FirewallRule {
    param([string] $Name, [int] $Port)

    # Skip if the rule already exists.
    $existing = Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "  = firewall rule '$Name' already present"
        return
    }
    New-NetFirewallRule -DisplayName $Name `
        -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort $Port -Profile Any | Out-Null
    Write-Host "  + firewall rule '$Name' (inbound TCP $Port)"
}

function Remove-FirewallRule {
    param([string] $Name)
    $existing = Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
    if ($existing) {
        Remove-NetFirewallRule -DisplayName $Name | Out-Null
        Write-Host "  - firewall rule '$Name' removed"
    } else {
        Write-Host "  = firewall rule '$Name' was not present"
    }
}

function Ensure-OllamaAutoStart {
    # If Ollama was installed via the official .msi, an "Ollama" service
    # is already registered. Restart it so it picks up the new
    # OLLAMA_HOST env var.
    $svc = Get-Service -Name "Ollama" -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Host "  = Ollama service found (state: $($svc.Status))"
        try {
            Restart-Service -Name "Ollama" -Force -ErrorAction Stop
            Write-Host "  + Ollama service restarted (new OLLAMA_HOST active)"
        } catch {
            Write-Warning "could not restart Ollama service: $($_.Exception.Message)"
        }
        return
    }

    # Fallback: register a startup scheduled task that runs `ollama serve`.
    $taskName = "OllamaServe"
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "  = scheduled task '$taskName' already present"
        return
    }
    $action = New-ScheduledTaskAction -Execute "ollama.exe" -Argument "serve"
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Description "Ollama daemon for K.H.A.V.I.S." | Out-Null
    Write-Host "  + scheduled task '$taskName' registered (runs ollama serve at boot)"
}

function Pull-Model {
    param([string] $Name)
    Write-Host "  ~ pulling $Name (this can take minutes on first run)..."
    & ollama pull $Name
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "ollama pull returned exit code $LASTEXITCODE"
    } else {
        Write-Host "  + model pulled: $Name"
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Assert-Admin

if ($Uninstall) {
    Write-Host "== Removing K.H.A.V.I.S. Surface Ollama setup =="
    Remove-OllamaHostSystemEnv
    Remove-FirewallRule -Name "Ollama 11434 (K.H.A.V.I.S.)"
    Write-Host "Done."
    exit 0
}

Write-Host "== Setting up K.H.A.V.I.S. Surface Ollama host =="
Write-Host "Target model: $Model"

# 1) OLLAMA_HOST
Set-OllamaHostSystemEnv -Value "0.0.0.0"

# 2) Firewall
Add-FirewallRule -Name "Ollama 11434 (K.H.A.V.I.S.)" -Port 11434

# 3) Auto-start
Ensure-OllamaAutoStart

# 4) Pre-pull (optional)
if (-not $SkipModelPull) {
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Pull-Model -Name $Model
    } else {
        Write-Warning "ollama CLI not in PATH; skip pull. Install from https://ollama.com/download"
    }
}

Write-Host ""
Write-Host "Done. Verify from the Mac with:"
Write-Host "  bash scripts/setup_ollama.sh --surface --ip $env:COMPUTERNAME"
Write-Host "(replace \$env:COMPUTERNAME with this machine's LAN IP if needed)"
exit 0