# -*- coding: utf-8 -*-
"""
Patch native_dialogs.py and app.js:
1. Make Windows native folder dialog TopMost so it forcibly pops up in front of the browser / Electron window.
2. Set Console::OutputEncoding = UTF-8 and add errors='replace' to avoid UnicodeDecodeError on Korean Windows.
3. Add safety timeout and auto-reset in app.js so browse button never gets stuck in '대기 중...' spinner.
4. Synchronize all modified files to DEPLOY_DIR (resources).
"""
import os
import shutil

ROOT_DIR = r"c:\daon\Daon agent System"
DEPLOY_DIR = r"C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources"

# 1. Update native_dialogs.py
native_dialogs_code = '''"""
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
    return _run_ps_dialog(ps_code).replace('\\\\', '/')


def select_file_dialog(workspace: str = '') -> str:
    """Open a native Windows file open dialog in foreground and return the selected file path."""
    ws_dir = workspace.replace('/', '\\\\').replace("'", "''")
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
    return _run_ps_dialog(ps_code).replace('\\\\', '/')
'''

native_dialogs_path = os.path.join(ROOT_DIR, "api", "api", "native_dialogs.py")
with open(native_dialogs_path, "w", encoding="utf-8") as f:
    f.write(native_dialogs_code)
print(f"[1] Updated {native_dialogs_path}")

# 2. Update app.js browseBtn logic with safety race
app_js_path = os.path.join(ROOT_DIR, "static", "v2", "js", "app.js")
with open(app_js_path, "r", encoding="utf-8") as f:
    app_js = f.read()

old_browse_block = """  if (browseBtn) {
    browseBtn.addEventListener('click', async () => {
      try {
        browseBtn.disabled = true;
        browseBtn.innerHTML = '<span class="material-symbols-outlined text-[16px] animate-spin">refresh</span><span>대기 중...</span>';
        const res = await DaonAPI.selectWorkspaceDialog();
        if (res && res.path && pathInput) {
          pathInput.value = res.path;
        }
      } catch (err) {
        alert('폴더 선택 창을 열지 못했습니다. 경로를 직접 입력해주세요.');
      } finally {
        browseBtn.disabled = false;
        browseBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">folder_open</span><span>찾아보기</span>';
      }
    });
  }"""

new_browse_block = """  if (browseBtn) {
    browseBtn.addEventListener('click', async () => {
      try {
        browseBtn.disabled = true;
        browseBtn.innerHTML = '<span class="material-symbols-outlined text-[16px] animate-spin">refresh</span><span>선택 대기...</span>';
        
        // 45초 세이프티 타임아웃 (스피너 무한 회전 방지)
        const timeoutPromise = new Promise((_, reject) =>
          setTimeout(() => reject(new Error('TIMEOUT')), 45000)
        );

        const res = await Promise.race([
          DaonAPI.selectWorkspaceDialog(),
          timeoutPromise
        ]);

        if (res && res.path && pathInput) {
          pathInput.value = res.path;
          if (typeof showVoiceToast === 'function') {
            showVoiceToast('📂 폴더 선택 완료: ' + res.path, '📂', 2500);
          }
        }
      } catch (err) {
        if (err.message !== 'TIMEOUT') {
          console.warn('[Workspace Dialog]', err);
        }
      } finally {
        browseBtn.disabled = false;
        browseBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">folder_open</span><span>찾아보기</span>';
      }
    });
  }"""

if old_browse_block in app_js:
    app_js = app_js.replace(old_browse_block, new_browse_block)
    print("[2] Updated browseBtn logic in app.js with Promise.race and safety timeout!")
    with open(app_js_path, "w", encoding="utf-8") as f:
        f.write(app_js)
else:
    print("[2] Warning: old_browse_block not found in app.js")

# 3. Synchronize to DEPLOY_DIR (resources)
if os.path.exists(DEPLOY_DIR):
    deploy_dialogs = os.path.join(DEPLOY_DIR, "api", "api", "native_dialogs.py")
    if os.path.exists(os.path.dirname(deploy_dialogs)):
        shutil.copy2(native_dialogs_path, deploy_dialogs)
        print(f"[3] Synced native_dialogs.py to {deploy_dialogs}")
    
    deploy_app_js = os.path.join(DEPLOY_DIR, "static", "v2", "js", "app.js")
    if os.path.exists(os.path.dirname(deploy_app_js)):
        shutil.copy2(app_js_path, deploy_app_js)
        print(f"[3] Synced app.js to {deploy_app_js}")

print("All patches completed successfully!")
