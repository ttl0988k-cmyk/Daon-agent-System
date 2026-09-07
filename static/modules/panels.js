// @ts-check
/**
 * DAON Panel Registry & Router
 * Core panel switcher and coordinator for modular sub-panels.
 * Sub-panels are loaded from static/modules/panels/*.js
 */

var _currentPanel = 'chat';

var _skillsData = null; // cached skills list

async function switchPanel(name) {
  _currentPanel = name;
  // Update nav tabs (original sidebar tabs)
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.toggle('active', t.dataset.panel === name));
  // Update panel views
  document.querySelectorAll('.panel-view').forEach(p => p.classList.remove('active'));
  const panelEl = $('panel' + name.charAt(0).toUpperCase() + name.slice(1));
  if (panelEl) panelEl.classList.add('active');

  if (name === 'tasks') {
    if (typeof clearCronBadge === 'function') clearCronBadge();
    await loadCrons();
  }
  if (name === 'skills') { await loadSkills(); setTimeout(loadSkillsHubPanel, 150); }
  if (name === 'memory') await loadMemory();
  if (name === 'workspaces') await loadWorkspacesPanel();
  if (name === 'profiles') await loadProfilesPanel();
  if (name === 'todos') loadTodos();
  if (name === 'artifacts' && typeof renderArtifactListSidebar === 'function') renderArtifactListSidebar();
  if (name === 'setup') { if (typeof renderSetupPackHistorySidebar === 'function') renderSetupPackHistorySidebar(); setTimeout(loadSetupPanel, 100); }
  if (name === 'checks' && typeof renderPreflightResultSidebar === 'function') renderPreflightResultSidebar('note');
  if (name === 'dashboard') { setTimeout(loadDashboard, 50); setTimeout(loadConfigScore, 100); }
  if (name === 'git') { cleanupGitPanel(); setTimeout(loadGitPanel, 50); }
  if (name === 'browser') { loadBrowserPanel(); }
  if (name === 'docs') { loadDocsPanel(); }
  if (name === 'integrations') { loadConnectorsPanel(); }
  if (name === 'mcp') { loadMcpPanel(); }
  if (name === 'plugins') { loadPluginsPanel(); }
}

// ── Cron panel ──
