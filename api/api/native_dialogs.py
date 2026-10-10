"""
Native Windows GUI dialogs helper for folder and file selection.

Provides PowerShell-based FolderBrowserDialog and OpenFileDialog
for the DAON Agent System web UI.
"""

import os
import subprocess
import logging

_logger = logging.getLogger(__name__)


def _run_ps_dialog(ps_code: str) -> str:
    """Run a PowerShell script block that returns a string path."""
    cmd = ["powershell", "-NoProfile", "-STA", "-Command", ps_code]
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=60
        )
        selected = res.stdout.strip()
        if selected == 'NON_INTERACTIVE':
            return ""
        return selected
    except subprocess.TimeoutExpired:
        _logger.warning("[dialog] PowerShell GUI dialog timed out after 60s")
        return ""
    except Exception as e:
        _logger.error(f"[dialog] PowerShell GUI dialog error: {e}")
        return ""


def select_workspace_dialog() -> str:
    """Open a native Windows folder browser dialog in foreground and return the selected path."""
    ps_code = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
        "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null;"
        "$form = New-Object System.Windows.Forms.Form;"
        "$form.TopMost = $true;"
        "$form.Opacity = 0;"
        "$form.ShowInTaskbar = $false;"
        "$form.WindowState = [System.Windows.Forms.FormWindowState]::Minimized;"
        "$form.Show();"
        "$form.BringToFront();"
        "$form.Activate();"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$d.AutoUpgradeEnabled = $true;"
        "$d.Description = '프로젝트 작업 폴더를 선택하세요';"
        "$d.ShowNewFolderButton = $true;"
        "$res = $d.ShowDialog($form);"
        "if ($res -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath };"
        "$form.Close();"
        "$form.Dispose();"
    )
    return _run_ps_dialog(ps_code).replace('\\', '/')


def select_file_dialog(workspace: str = '') -> str:
    """Open a native Windows file open dialog in foreground and return the selected file path."""
    ws_dir = workspace.replace('/', '\\').replace("'", "''")
    ps_code = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
        "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null;"
        "$form = New-Object System.Windows.Forms.Form;"
        "$form.TopMost = $true;"
        "$form.Opacity = 0;"
        "$form.ShowInTaskbar = $false;"
        "$form.WindowState = [System.Windows.Forms.FormWindowState]::Minimized;"
        "$form.Show();"
        "$form.BringToFront();"
        "$form.Activate();"
        "$d = New-Object System.Windows.Forms.OpenFileDialog;"
        f"$d.InitialDirectory = '{ws_dir}';"
        "$d.Filter = 'All Files (*.*)|*.*';"
        "$d.Title = '파일 선택';"
        "$res = $d.ShowDialog($form);"
        "if ($res -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.FileName };"
        "$form.Close();"
        "$form.Dispose();"
    )
    return _run_ps_dialog(ps_code).replace('\\', '/')
