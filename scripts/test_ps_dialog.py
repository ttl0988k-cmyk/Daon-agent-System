# -*- coding: utf-8 -*-
import subprocess

def test_ps():
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
        "$d.Description = '작업 폴더 선택';"
        "$d.ShowNewFolderButton = $true;"
        "Write-Output 'PS_READY';"
        "$form.Close();"
        "$form.Dispose();"
    )
    cmd = ["powershell", "-NoProfile", "-STA", "-Command", ps_code]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    print("RC:", res.returncode)
    print("STDOUT:", res.stdout.strip())
    print("STDERR:", res.stderr.strip())

if __name__ == '__main__':
    test_ps()
