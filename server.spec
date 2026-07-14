# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['server.py'],
    pathex=['hermes-agent'],
    binaries=[],
    datas=[('index.html', '.'), ('static', 'static'), ('skills', 'skills'), ('hermes-agent', 'hermes-agent'), ('config.yaml', '.'), ('.env', '.'), ('data', 'data'), ('plans', 'plans')],
    hiddenimports=['agent.openai_client_lifecycle', 'agent.stream_handler', 'agent.fallback', 'agent.tool_executor', 'agent.codex_adapter', 'api.dynamic_jobs', 'api.native_dialogs', 'api.routes', 'api.routes.git_routes', 'api.routes.demo_to_skill_routes', 'api.demo_to_skill', 'api.routes.dashboard_routes', 'api.routes.whisper_routes', 'api.routes.browser_routes', 'api.routes.docs_routes', 'api.routes.integration_routes', 'api.routes.diff_routes', 'api.routes.mode_routes', 'api.routes.approval_routes', 'api.routes.settings_routes', 'api.approval', 'greenlet', 'PIL', 'PIL.Image', 'api.dynamic_hermes', 'api.dynamic.compiler', 'api.dynamic.orchestrator', 'api.dynamic.runner', 'api.dynamic.planner', 'api.dynamic.merger', 'api.dynamic.state', 'api.dynamic.limits', 'api.dynamic.plan_validator', 'api.dynamic.skill_extractor', 'api.dynamic.dag_utils', 'api.dynamic.direct_calls', 'api.dynamic.auth', 'api.dynamic.skill_retriever', 'api.dynamic.model_selector', 'api.dynamic.experience_db', 'api.dynamic.logging_utils', 'api.routes.mcp_routes', 'api.mcp_client', 'api.managers', 'api.managers.model_manager', 'api.score_engine', 'api.routes.score_routes', 'api.setup_generator', 'api.routes.setup_routes', 'api.mcp_recommender', 'api.sync_watcher', 'api.routes.sync_routes', 'api.routes.skills_hub_routes', 'api.skills_recommender', 'api.style_card', 'api.dynamic.style_mixer', 'api.dynamic.component_retriever', 'api.dynamic.style_card_retriever', 'api.dynamic.style_card_extractor', 'api.routes.style_card_routes', 'api.routes.debate_routes', 'api.kakao_bridge', 'api.routes.kakao_routes', 'api.mcp', 'api.mcp.daon_design_mcp'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
