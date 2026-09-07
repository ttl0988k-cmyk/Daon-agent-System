// @ts-check
/**
 * Test Phase 3 Electron Modularization
 * Verifies that all modules can be required, instantiated, and export their expected APIs.
 */
const assert = require('assert');

console.log('--- Testing Phase 3 Electron Modules ---');

// 1. Logger
const { mlog, merr, mainLogInit } = require('../electron/src/Logger');
assert.strictEqual(typeof mlog, 'function');
assert.strictEqual(typeof merr, 'function');
assert.strictEqual(typeof mainLogInit, 'function');
console.log('✓ Logger module exports verified.');

// 2. TempCleaner
const { cleanupOrphanedTemp } = require('../electron/src/TempCleaner');
assert.strictEqual(typeof cleanupOrphanedTemp, 'function');
console.log('✓ TempCleaner module exports verified.');

// 3. HeaderNormalizer
const { normalizeChromeHeaders, attachChromeHeaderNormalization } = require('../electron/src/HeaderNormalizer');
assert.strictEqual(typeof normalizeChromeHeaders, 'function');
assert.strictEqual(typeof attachChromeHeaderNormalization, 'function');

// Test header normalization logic
const testHeaders = {
  'sec-ch-ua': 'OldUA',
  'Accept-Language': 'ko',
};
const normalized = normalizeChromeHeaders(testHeaders, 'http://localhost');
assert.strictEqual(normalized['sec-ch-ua'], '"Chromium";v="138", "Google Chrome";v="138", "Not)A;Brand";v="99"');
assert.strictEqual(normalized['Accept-Language'], 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7');
console.log('✓ HeaderNormalizer logic verified.');

// 4. ServerSupervisor
const { ServerSupervisor } = require('../electron/src/ServerSupervisor');
assert.strictEqual(typeof ServerSupervisor, 'function');

const supervisor = new ServerSupervisor({
  mlog: () => {},
  merr: () => {},
});
assert.strictEqual(typeof supervisor.findFreePort, 'function');
assert.strictEqual(typeof supervisor.isPortListening, 'function');
assert.strictEqual(typeof supervisor.probeHealth, 'function');
assert.strictEqual(typeof supervisor.probeHealthStable, 'function');
assert.strictEqual(typeof supervisor.probeHealthGrace, 'function');
assert.strictEqual(typeof supervisor.checkServerHealth, 'function');
assert.strictEqual(typeof supervisor.adoptRunningServer, 'function');
assert.strictEqual(typeof supervisor.startPythonProcess, 'function');
assert.strictEqual(typeof supervisor.startTtsProcess, 'function');
assert.strictEqual(typeof supervisor.startWatchdog, 'function');
assert.strictEqual(typeof supervisor.stopWatchdog, 'function');
assert.strictEqual(typeof supervisor.shutdown, 'function');
console.log('✓ ServerSupervisor API & 5-in-1 health probe methods verified.');

// 5. TrayManager
const { TrayManager } = require('../electron/src/TrayManager');
assert.strictEqual(typeof TrayManager, 'function');
const trayManager = new TrayManager({
  getMainWindow: () => null,
  getServerPort: () => 9090,
  onQuit: () => {},
});
assert.strictEqual(typeof trayManager.findTrayIcon, 'function');
assert.strictEqual(typeof trayManager.buildTrayMenu, 'function');
assert.strictEqual(typeof trayManager.createTray, 'function');
console.log('✓ TrayManager API verified.');

// 6. WindowManager
const { WindowManager } = require('../electron/src/WindowManager');
assert.strictEqual(typeof WindowManager, 'function');
const windowManager = new WindowManager({
  mlog: () => {},
  merr: () => {},
});
assert.strictEqual(typeof windowManager.createSplashWindow, 'function');
assert.strictEqual(typeof windowManager.createMainWindow, 'function');
assert.strictEqual(typeof windowManager.loadAppUi, 'function');
console.log('✓ WindowManager API verified.');

// 7. TabManager
const { TabManager } = require('../electron/src/TabManager');
assert.strictEqual(typeof TabManager, 'function');
assert.strictEqual(TabManager.MAX_TAB_RECOVERY, 5);
assert.strictEqual(TabManager.TAB_RECOVERY_WINDOW_MS, 300000);
console.log('✓ TabManager API verified.');

// 8. IpcHandlers
const { registerIpcHandlers } = require('../electron/src/IpcHandlers');
assert.strictEqual(typeof registerIpcHandlers, 'function');
console.log('✓ IpcHandlers API verified.');

console.log('\n[ALL PHASE 3 ELECTRON UNIT TESTS PASSED!]');
