"""
Native Windows GUI dialogs helper for folder and file selection.

Provides PowerShell-based FolderBrowserDialog and OpenFileDialog
for the DAON Agent System web UI.

Exported:
- select_workspace_dialog(): opens folder picker, returns path string
- select_file_dialog(workspace: str): opens file picker, returns path string
"""

import os
import subprocess
import traceback


# ── Common PowerShell dialog helper ──

def _is_non_interactive():
    """Return True if the current session is non-interactive (e.g., started by AI agent)."""
    return any(k in os.environ for k in ('ANTIGRAVITY_EDITOR_APP_ROOT', 'VSCODE_PID'))


def _run_ps_dialog(ps_code: str) -> str:
    """Run a PowerShell script block that returns a string path.
    
    Raises RuntimeError if the session is non-interactive or the dialog is cancelled.
    Returns the selected path (empty string if cancelled).
    """
    if _is_non_interactive():
        raise RuntimeError(
            "Native dialogs are disabled when the server is started by the AI agent. "
            "Please enter the path manually or run the server directly."
        )
    
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_code]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding='utf-8', timeout=600)
    selected = res.stdout.strip()
    if selected == 'NON_INTERACTIVE':
        raise RuntimeError(
            "GUI dialogs are not supported in this non-interactive/headless session."
        )
    return selected


# ── Public API ──

def select_workspace_dialog() -> str:
    """Open a native Windows folder browser dialog and return the selected path."""
    ps_code = (
        "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null;"
        "if (-not [System.Windows.Forms.SystemInformation]::UserInteractive) {"
        "  Write-Output 'NON_INTERACTIVE';"
        "  exit;"
        "}"
        "$objForm = New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$objForm.Description = 'Select Workspace Folder';"
        "$objForm.ShowNewFolderButton = $true;"
        "$Show = $objForm.ShowDialog();"
        "if ($Show -eq 'OK') { Write-Output $objForm.SelectedPath }"
    )
    return _run_ps_dialog(ps_code).replace('\\', '/')


def select_file_dialog(workspace: str = '') -> str:
    """Open a native Windows file open dialog and return the selected file path."""
    ws_dir = workspace.replace('/', '\\').replace("'", "''")
    
    ps_code = (
        "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null;"
        "if (-not [System.Windows.Forms.SystemInformation]::UserInteractive) {"
        "  Write-Output 'NON_INTERACTIVE';"
        "  exit;"
        "}"
        "$objForm = New-Object System.Windows.Forms.OpenFileDialog;"
        f"$objForm.InitialDirectory = '{ws_dir}';"
        "$objForm.Filter = 'All Files (*.*)|*.*';"
        "$objForm.Title = 'Select File to Open';"
        "$Show = $objForm.ShowDialog();"
        "if ($Show -eq 'OK') { Write-Output $objForm.FileName }"
    )
    return _run_ps_dialog(ps_code).replace('\\', '/')
