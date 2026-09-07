// @ts-check
/**
 * TempCleaner
 * Cleans up orphaned PyInstaller _MEI* and playwright-artifacts-* temporary directories.
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');

function cleanupOrphanedTemp() {
  setTimeout(() => {
    const tempDir = process.env.TEMP || os.tmpdir();
    let entries;
    try {
      entries = fs.readdirSync(tempDir);
    } catch (e) {
      console.warn('[Cleanup] Cannot read temp dir:', e.message);
      return;
    }

    // Find which _MEI folders are currently in use by running server.exe processes
    let activeMEIs = new Set();
    try {
      const psOut = execSync(
        'powershell -NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process -Filter \\"name=\'server.exe\'\\" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ExecutablePath"',
        { windowsHide: true, encoding: 'utf-8', timeout: 5000 }
      );
      for (const line of psOut.split(/\r?\n/)) {
        const match = line.match(/(_MEI\d+)/i);
        if (match) activeMEIs.add(match[1]);
      }
    } catch (_) { }

    let freedCount = 0;
    for (const entry of entries) {
      const isMEI = entry.startsWith('_MEI');
      const isPlaywright = entry.startsWith('playwright-artifacts-');
      if (!isMEI && !isPlaywright) continue;
      if (isMEI && activeMEIs.has(entry)) continue;

      const fullPath = path.join(tempDir, entry);
      try {
        const stat = fs.statSync(fullPath);
        if (!stat.isDirectory()) continue;
        fs.rmSync(fullPath, { recursive: true, force: true });
        freedCount++;
      } catch (_) { }
    }
    if (freedCount > 0) {
      console.log(`[Cleanup] Removed ${freedCount} orphaned temp folder(s).`);
    }
  }, 5000);
}

module.exports = {
  cleanupOrphanedTemp,
};
