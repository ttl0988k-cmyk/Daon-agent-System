// @ts-check
/**
 * Test Phase 4 Frontend Modularization & Memory Leak Prevention
 * Verifies that:
 * 1. All 8 modular panel files + panels.js + chat.js have valid JavaScript syntax.
 * 2. All expected global panel functions are properly exported to the window context.
 * 3. index.html includes all 8 sub-panels in the correct order before panels.js.
 * 4. Settings panel implements AbortController cleanup logic for event listeners.
 * 5. chat.js ensures explicit sse.close() on completion.
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

console.log('--- Testing Phase 4 Frontend Modularization ---');

const ROOT = path.resolve(__dirname, '..');
const PANELS_DIR = path.join(ROOT, 'static', 'modules', 'panels');
const PANELS_FILE = path.join(ROOT, 'static', 'modules', 'panels.js');
const CHAT_FILE = path.join(ROOT, 'static', 'modules', 'chat.js');
const INDEX_HTML = path.join(ROOT, 'index.html');

const PANEL_FILES = [
  'cron_panel.js',
  'todo_panel.js',
  'skills_panel.js',
  'workspace_panel.js',
  'profiles_panel.js',
  'memory_panel.js',
  'settings_panel.js',
  'demo_panel.js',
];

// 1. Check file existence and basic size
for (const file of PANEL_FILES) {
  const filePath = path.join(PANELS_DIR, file);
  assert.ok(fs.existsSync(filePath), `Panel file missing: ${file}`);
  const stat = fs.statSync(filePath);
  assert.ok(stat.size > 0, `Panel file is empty: ${file}`);
}
console.log('✓ All 8 panel files exist with valid sizes.');

// 2. Simulated DOM / Window Environment using vm
const mockElement = () => ({
  addEventListener: () => {},
  removeEventListener: () => {},
  innerHTML: '',
  value: '',
  checked: false,
  classList: { add: () => {}, remove: () => {}, contains: () => false, toggle: () => {} },
  style: {},
  querySelector: () => mockElement(),
  querySelectorAll: () => [],
  appendChild: () => {},
  setAttribute: () => {},
  getAttribute: () => '',
  focus: () => {},
});

const sandbox = {
  window: {},
  document: {
    getElementById: () => mockElement(),
    querySelector: () => mockElement(),
    querySelectorAll: () => [],
    createElement: () => mockElement(),
    addEventListener: () => {},
    removeEventListener: () => {},
    body: mockElement(),
  },
  $: (id) => sandbox.document.getElementById(id),
  api: () => Promise.resolve({}),
  showToast: () => {},
  esc: (s) => s || '',
  renderMd: (s) => s || '',
  State: { sessions: [], activeSessionId: null },
  console: console,
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  AbortController: global.AbortController,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  setInterval: setInterval,
  clearInterval: clearInterval,
};
sandbox.window = sandbox;
const context = vm.createContext(sandbox);

// 3. Load each panel file into sandbox and verify syntax & exported globals
for (const file of PANEL_FILES) {
  const code = fs.readFileSync(path.join(PANELS_DIR, file), 'utf8');
  try {
    vm.runInContext(code, context);
  } catch (err) {
    assert.fail(`Syntax or execution error in ${file}: ${err.message}`);
  }
}
console.log('✓ All 8 panel modules successfully evaluated in VM sandbox.');

// 4. Load panels.js (router)
const panelsCode = fs.readFileSync(PANELS_FILE, 'utf8');
try {
  vm.runInContext(panelsCode, context);
} catch (err) {
  assert.fail(`Syntax or execution error in panels.js: ${err.message}`);
}
console.log('✓ panels.js router evaluated successfully.');

// 5. Verify exported global functions
const expectedFunctions = [
  // Cron (cron_panel.js)
  'loadCrons', 'toggleCronForm', 'toggleCron', 'cronRun', 'cronPause', 'cronResume', 'cronEditOpen', 'cronEditSave', 'cronDelete', 'clearCronBadge', 'startCronPolling',
  // Todo (todo_panel.js)
  'loadTodos', 'clearConversation',
  // Skills (skills_panel.js)
  'loadSkills', 'renderSkills', 'filterSkills', 'openSkill', 'openSkillFile', 'toggleSkillForm', 'submitSkillSave', 'toggleMemoryEdit', 'closeMemoryEdit', 'submitMemorySave',
  // Workspace (workspace_panel.js)
  'loadWorkspaceList', 'renderWorkspaceDropdown', 'toggleWsDropdown', 'closeWsDropdown', 'loadWorkspacesPanel', 'renderWorkspacesPanel', 'pickWorkspaceFolder', 'removeWorkspace', 'switchToWorkspace',
  // Profiles (profiles_panel.js)
  'loadProfilesPanel', 'renderProfileDropdown', 'toggleProfileDropdown', 'closeProfileDropdown', 'switchToProfile', 'toggleProfileForm', 'submitProfileCreate', 'deleteProfile',
  // Memory (memory_panel.js)
  'loadMemory', 'loadMemoryStore',
  // Settings (settings_panel.js)
  'toggleSettings', '_closeSettingsPanel', 'loadSettingsPanel', 'loadProviderManagement', 'saveProvider', 'deleteProvider', 'saveSettings', 'signOut', 'disableAuth',
  // Demo (demo_panel.js)
  'trackBackgroundError', 'showErrorBanner', 'navigateToErrorSession', 'dismissErrorBanner', 'openDemoSkill', 'resetDemoUI', 'startDemoRecording', 'stopDemoRecording', 'cancelDemoRecording',
  // Router (panels.js)
  'switchPanel',
];

for (const fn of expectedFunctions) {
  assert.strictEqual(
    typeof sandbox[fn],
    'function',
    `Expected global function "${fn}" is missing or not a function in window scope!`
  );
}
console.log(`✓ All ${expectedFunctions.length} expected global panel functions verified.`);

// 6. Verify index.html includes scripts in correct sequence
const htmlContent = fs.readFileSync(INDEX_HTML, 'utf8');
const scriptMatches = [...htmlContent.matchAll(/<script\s+src="([^"]+)"><\/script>/g)].map(m => m[1].replace(/^\//, ''));

for (const file of PANEL_FILES) {
  const expectedPath = `static/modules/panels/${file}`;
  assert.ok(
    scriptMatches.includes(expectedPath),
    `index.html does not include script tag for: ${expectedPath}`
  );
}

const panelsIndex = scriptMatches.indexOf('static/modules/panels.js');
assert.ok(panelsIndex > -1, 'index.html missing static/modules/panels.js');

for (const file of PANEL_FILES) {
  const expectedPath = `static/modules/panels/${file}`;
  const idx = scriptMatches.indexOf(expectedPath);
  assert.ok(
    idx < panelsIndex,
    `Sub-panel script ${expectedPath} (index ${idx}) must be loaded BEFORE panels.js (index ${panelsIndex})`
  );
}
console.log('✓ index.html script inclusion order verified (all 8 sub-panels load before panels.js).');

// 7. Verify AbortController pattern in settings_panel.js
const settingsCode = fs.readFileSync(path.join(PANELS_DIR, 'settings_panel.js'), 'utf8');
assert.ok(
  settingsCode.includes('_settingsAbortController'),
  'settings_panel.js must define _settingsAbortController'
);
assert.ok(
  settingsCode.includes('_settingsAbortController.abort()'),
  'settings_panel.js must call abort() to cleanly drop accumulated event listeners'
);
assert.ok(
  settingsCode.includes('{ signal }'),
  'settings_panel.js must pass { signal } to addEventListener calls'
);
console.log('✓ settings_panel.js AbortController event listener cleanup verified.');

// 8. Verify SSE close logic in chat.js
const chatCode = fs.readFileSync(CHAT_FILE, 'utf8');
assert.ok(
  chatCode.includes('if (sse) sse.close()'),
  'chat.js must explicitly invoke sse.close() inside finish() to prevent dangling SSE connections'
);
console.log('✓ chat.js stream connection closure verified.');

console.log('\n========================================');
console.log('Phase 4 Frontend Verification: ALL PASSED!');
console.log('========================================');
process.exit(0);
