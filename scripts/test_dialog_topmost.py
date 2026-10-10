# -*- coding: utf-8 -*-
import subprocess

def test_topmost_dialog():
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
        "$d.Description = '작업 폴더를 선택하세요';"
        "$d.ShowNewFolderButton = $true;"
        "Write-Output 'TOPMOST_DIALOG_VERIFIED';"
        "$form.Close();"
        "$form.Dispose();"
    )
    cmd = ["powershell", "-NoProfile", "-STA", "-Command", ps_code]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
    print("RC:", res.returncode)
    print("OUT:", res.stdout.strip())

if __name__ == '__main__':
    test_topmost_dialog()
