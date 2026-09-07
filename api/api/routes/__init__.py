"""Hermes Web UI — Route handler registry.

Aggregates all GET/POST route handlers from sub-modules using O(1) dictionary dispatch.
"""

import logging
from urllib.parse import parse_qs
from api.helpers import j, bad, read_body
from api.config import load_settings

_logger = logging.getLogger(__name__)

from api.routes.admin_routes import (
    _LOGIN_PAGE_HTML,
    handle_get_index,
    handle_get_login,
    handle_get_auth_status,
    handle_get_favicon,
    handle_get_health,
    handle_get_models,
    handle_serve_static,
    handle_serve_assets,
    handle_get_approval_pending,
    handle_get_approval_inject,
    handle_get_crons,
    handle_get_cron_output,
    handle_get_cron_recent,
    handle_get_skills,
    handle_get_skill_content,
    handle_get_memory,
    handle_get_memory_facts,
    handle_get_memory_profile,
    handle_get_memory_summaries,
    handle_get_memory_store_stats,
    handle_get_system_status,
    handle_get_agent_inbox,
    handle_post_agent_message,
    handle_post_approval_respond,
    handle_post_skill_promote,
    handle_post_skill_reject,
    handle_post_skill_save,
    handle_post_skill_delete,
    handle_post_memory_write,
    handle_post_memory_fact_delete,
    handle_post_memory_profile_set,
    handle_get_memory_reviews,
    handle_post_memory_review_resolve,
    handle_post_cron_create,
    handle_post_cron_update,
    handle_post_cron_delete,
    handle_post_cron_run,
    handle_post_cron_pause,
    handle_post_cron_resume,
    handle_post_auth_login,
    handle_post_auth_logout,
    handle_get_patches,
    handle_post_patch_register,
    handle_post_patch_delete,
    handle_post_patch_update,
    handle_post_patch_seed,
)
from api.routes.session_routes import (
    handle_get_session,
    handle_get_sessions,
    handle_get_session_export,
    handle_get_sessions_search,
    handle_get_projects,
    handle_post_session_new,
    handle_post_sessions_cleanup,
    handle_post_session_rename,
    handle_post_session_update,
    handle_post_session_delete,
    handle_post_session_clear,
    handle_post_session_truncate,
    handle_post_session_pin,
    handle_post_session_archive,
    handle_post_session_move,
    handle_post_session_import,
    handle_post_session_import_cli,
    handle_post_project_create,
    handle_post_project_rename,
    handle_post_project_delete,
)
from api.routes.chat_routes import (
    handle_get_stream_status,
    handle_get_chat_cancel,
    handle_post_chat_cancel,
    handle_get_sse_stream,
    handle_post_chat_start,
    handle_post_chat_sync,
)
from api.routes.file_routes import (
    handle_get_list_dir,
    handle_get_file_raw,
    handle_get_file_read,
    handle_get_git_info,
    handle_get_workspaces,
    handle_post_upload,
    handle_post_file_delete,
    handle_post_file_save,
    handle_post_file_create,
    handle_post_file_rename,
    handle_post_create_dir,
    handle_post_workspace_add,
    handle_post_workspace_remove,
    handle_post_workspace_rename,
    handle_get_fs_list,
    handle_get_workspace_select,
    handle_get_file_select,
    handle_get_preview,
)
from api.routes.settings_routes import (
    handle_get_settings,
    handle_get_profiles,
    handle_get_profile_active,
    handle_post_settings,
    handle_post_profile_switch,
    handle_post_profile_create,
    handle_post_profile_delete,
    handle_get_providers,
    handle_post_provider_add,
    handle_post_provider_delete,
    handle_post_provider_fetch_models,
    handle_post_provider_update_models,
    handle_post_provider_refresh_models,
)
from api.routes.dashboard_routes import handle_get_dashboard_metrics
from api.routes.git_routes import (
    handle_get_git_status,
    handle_get_git_diff,
    handle_get_git_log,
    handle_get_git_conflict,
    handle_post_git_commit,
    handle_post_git_push,
    handle_post_git_pull,
    handle_post_git_stage,
    handle_post_git_unstage,
    handle_post_git_discard,
)
from api.routes.whisper_routes import handle_post_whisper_transcribe
from api.routes.docs_routes import (
    handle_post_docs_generate,
    handle_get_docs_status,
    handle_get_docs_list,
)
from api.routes.integration_routes import (
    handle_get_integration_config,
    handle_post_integration_config,
    handle_post_slack_send,
    handle_post_slack_test,
    handle_post_notion_create,
    handle_post_notion_test,
)
from api.routes.diff_routes import (
    handle_post_file_apply_diff,
    handle_post_file_preview_diff,
    handle_post_file_apply_preview,
    handle_post_file_reject_preview,
    handle_get_checkpoints,
    handle_get_diff_history,
    handle_get_diff_preview,
    handle_post_checkpoint_rollback,
    handle_post_checkpoint_delete,
)
from api.routes.approval_routes import (
    handle_get_approval_pending,
    handle_get_approval_history,
    handle_post_approval_approve,
    handle_post_approval_reject,
    handle_post_skill_save_approve,
    handle_post_skill_save_reject,
)
from api.routes.mode_routes import (
    handle_get_modes,
    handle_get_mode,
    handle_post_mode,
    handle_post_mode_intent,
)
from api.routes.browser_routes import (
    handle_get_browser_status,
    handle_get_browser_grid,
    handle_get_browser_proxy,
    handle_get_browser_recommend,
    handle_post_browser_navigate,
    handle_post_browser_sync_url,
    handle_post_browser_snapshot,
    handle_post_browser_click,
    handle_post_browser_type,
    handle_post_browser_screenshot,
    handle_post_browser_execute,
    handle_post_browser_close,
    handle_post_browser_back,
    handle_post_browser_forward,
    handle_post_browser_tabs,
    handle_post_browser_switch_tab,
    handle_post_browser_batch,
    handle_post_browser_focus,
    handle_post_browser_close_tab,
)
from api.routes.setup_routes import (
    handle_post_setup_generate,
    handle_get_setup_preview,
    handle_get_setup_detect,
)
from api.routes.mcp_routes import (
    handle_get_mcp_servers,
    handle_get_mcp_presets,
    handle_get_mcp_recommend,
    handle_get_capability_diagnose,
    handle_get_capability_tests,
    handle_get_capability_mappings,
    handle_post_capability_route,
    handle_post_mcp_server_add,
    handle_post_mcp_server_remove,
    handle_post_mcp_server_connect,
    handle_post_mcp_server_disconnect,
    handle_post_mcp_server_add_preset,
    handle_post_mcp_tool_call,
    handle_post_mcp_exchange_ott,
)
from api.routes.score_routes import handle_get_score_evaluate
from api.routes.speak_routes import handle_tts
from api.routes.demo_to_skill_routes import (
    handle_get_demo_status,
    handle_get_demo_events,
    handle_post_demo_start,
    handle_post_demo_stop,
    handle_post_demo_cancel,
    handle_post_demo_text_workflow,
    handle_post_demo_add_event,
    handle_post_skill_approve,
    handle_post_skill_reject,
)
from api.routes.debate_routes import (
    handle_post_debate_start,
    handle_post_debate_next,
    handle_post_debate_cancel,
)
from api.routes.sync_routes import (
    handle_get_sync_status,
    handle_post_sync_start,
    handle_post_sync_stop,
    handle_post_sync_hook_install,
    handle_post_sync_hook_uninstall,
)
from api.routes.system_routes import (
    handle_get_build_info,
    handle_get_last_restart,
    handle_post_last_restart_ack,
)
from api.routes.skills_hub_routes import (
    handle_get_skills_hub_search,
    handle_get_skills_hub_sources,
    handle_get_skills_hub_recommend,
    handle_post_skills_hub_install,
    handle_post_skills_from_github,
)
from api.routes.dynamic_routes import (
    handle_post_dynamic_run,
    handle_get_dynamic_status,
    handle_post_dynamic_approve,
    handle_post_dynamic_answer,
    handle_post_dynamic_cancel,
)
from api.routes.style_card_routes import (
    handle_get_style_cards,
    handle_get_style_card_content,
    handle_get_style_cards_categories,
    handle_post_style_card_extract,
    handle_post_style_card_save,
    handle_post_style_card_delete,
    handle_post_style_cards_rebuild_index,
)
from api.routes.creative_director_routes import (
    handle_get_creative_director_health,
    handle_get_creative_director_cards,
    handle_post_creative_director_brief,
    handle_post_creative_director_card_extract,
)
from api.routes.kakao_routes import (
    handle_get_kakao_status,
    handle_post_kakao_send,
)
from api.routes.mobile_routes import (
    handle_get_mobile,
    handle_get_mobile_js,
    handle_get_mobile_conversations,
    handle_post_mobile_login,
    handle_post_mobile_conversations,
    handle_post_mobile_messages,
)
from api.routes.plugin_routes import (
    handle_get_plugin_credentials,
    handle_get_plugin_credentials_pending,
    handle_get_plugins,
    handle_get_plugins_state,
    handle_post_plugin_credentials_remove,
    handle_post_plugin_credentials_set,
    handle_post_plugin_disable,
    handle_post_plugin_enable,
    handle_post_plugin_remove,
    handle_post_plugin_session,
    handle_post_plugins_import,
)


# ── Special Route Helpers ──

def _handle_get_approval_inject_test(handler, parsed) -> bool:
    if handler.client_address[0] != '127.0.0.1':
        return j(handler, {'error': 'not found'}, status=404)
    return handle_get_approval_inject(handler, parsed)


def _handle_get_browser_status_logged(handler, parsed) -> bool:
    _logger.debug("Browser status route matched, calling handle_get_browser_status")
    result = handle_get_browser_status(handler, parsed)
    _logger.debug("handle_get_browser_status returned: %s", result)
    return result


def _handle_get_plugin_credentials_subpath(handler, parsed) -> bool:
    rest = parsed.path[len('/api/plugins/'):]
    parts = rest.split('/')
    if len(parts) == 2 and parts[1] == 'credentials':
        plugin_name = parts[0]
        return handle_get_plugin_credentials(handler, parsed, plugin_name)
    return False


def _handle_post_plugin_subpath(handler, body, parsed) -> bool:
    rest = parsed.path[len('/api/plugins/'):]
    parts = rest.split('/')
    if len(parts) == 2:
        plugin_name, action = parts
        if action == 'enable':
            return handle_post_plugin_enable(handler, body, plugin_name)
        if action == 'disable':
            return handle_post_plugin_disable(handler, body, plugin_name)
        if action == 'session':
            return handle_post_plugin_session(handler, body, plugin_name)
        if action == 'remove':
            return handle_post_plugin_remove(handler, body, plugin_name)
        if action == 'credentials':
            return handle_post_plugin_credentials_set(handler, body, plugin_name)
    if len(parts) == 3 and parts[1] == 'credentials' and parts[2] == 'remove':
        return handle_post_plugin_credentials_remove(handler, body, parts[0])
    return False


# ── O(1) GET Route Registry ──
GET_EXACT_ROUTES = {
    '/': handle_get_index,
    '/api/agent/inbox': handle_get_agent_inbox,
    '/api/approval/history': handle_get_approval_history,
    '/api/approval/inject_test': _handle_get_approval_inject_test,
    '/api/approval/pending': handle_get_approval_pending,
    '/api/auth/status': handle_get_auth_status,
    '/api/browser/grid': handle_get_browser_grid,
    '/api/browser/proxy': handle_get_browser_proxy,
    '/api/browser/recommend': handle_get_browser_recommend,
    '/api/browser/sessions': handle_get_browser_grid,
    '/api/browser/status': _handle_get_browser_status_logged,
    '/api/capability/diagnose': handle_get_capability_diagnose,
    '/api/capability/mappings': handle_get_capability_mappings,
    '/api/capability/tests': handle_get_capability_tests,
    '/api/chat/cancel': handle_get_chat_cancel,
    '/api/chat/stream': handle_get_sse_stream,
    '/api/chat/stream/status': handle_get_stream_status,
    '/api/checkpoints': handle_get_checkpoints,
    '/api/creative-director/cards': handle_get_creative_director_cards,
    '/api/creative-director/health': handle_get_creative_director_health,
    '/api/crons': handle_get_crons,
    '/api/crons/output': handle_get_cron_output,
    '/api/crons/recent': handle_get_cron_recent,
    '/api/dashboard/metrics': handle_get_dashboard_metrics,
    '/api/demo/events': handle_get_demo_events,
    '/api/demo/status': handle_get_demo_status,
    '/api/diff/history': handle_get_diff_history,
    '/api/diff/preview': handle_get_diff_preview,
    '/api/docs/list': handle_get_docs_list,
    '/api/docs/status': handle_get_docs_status,
    '/api/dynamic/status': handle_get_dynamic_status,
    '/api/file': handle_get_file_read,
    '/api/file/raw': handle_get_file_raw,
    '/api/file/select': handle_get_file_select,
    '/api/fs/list': handle_get_fs_list,
    '/api/git-info': handle_get_git_info,
    '/api/git/conflicts': handle_get_git_conflict,
    '/api/git/diff': handle_get_git_diff,
    '/api/git/log': handle_get_git_log,
    '/api/git/status': handle_get_git_status,
    '/api/integration/config': handle_get_integration_config,
    '/api/kakao/status': handle_get_kakao_status,
    '/api/list': handle_get_list_dir,
    '/api/mcp/presets': handle_get_mcp_presets,
    '/api/mcp/recommend': handle_get_mcp_recommend,
    '/api/mcp/servers': handle_get_mcp_servers,
    '/api/memory': handle_get_memory,
    '/api/memory/facts': handle_get_memory_facts,
    '/api/memory/profile': handle_get_memory_profile,
    '/api/memory/reviews': handle_get_memory_reviews,
    '/api/memory/store/stats': handle_get_memory_store_stats,
    '/api/memory/summaries': handle_get_memory_summaries,
    '/api/mobile/conversations': handle_get_mobile_conversations,
    '/api/mode': handle_get_mode,
    '/api/models': handle_get_models,
    '/api/modes': handle_get_modes,
    '/api/patches': handle_get_patches,
    '/api/plugins': handle_get_plugins,
    '/api/plugins/credentials/pending': handle_get_plugin_credentials_pending,
    '/api/plugins/state': handle_get_plugins_state,
    '/api/profile/active': handle_get_profile_active,
    '/api/profiles': handle_get_profiles,
    '/api/projects': handle_get_projects,
    '/api/providers': handle_get_providers,
    '/api/score/evaluate': handle_get_score_evaluate,
    '/api/session': handle_get_session,
    '/api/session/export': handle_get_session_export,
    '/api/sessions': handle_get_sessions,
    '/api/sessions/search': handle_get_sessions_search,
    '/api/settings': handle_get_settings,
    '/api/setup/detect': handle_get_setup_detect,
    '/api/setup/preview': handle_get_setup_preview,
    '/api/skills': handle_get_skills,
    '/api/skills/content': handle_get_skill_content,
    '/api/skills/hub/sources': handle_get_skills_hub_sources,
    '/api/skills/recommend': handle_get_skills_hub_recommend,
    '/api/skills/search': handle_get_skills_hub_search,
    '/api/speak/tts': handle_tts,
    '/api/style-cards': handle_get_style_cards,
    '/api/style-cards/categories': handle_get_style_cards_categories,
    '/api/style-cards/content': handle_get_style_card_content,
    '/api/sync/status': handle_get_sync_status,
    '/api/system/build-info': handle_get_build_info,
    '/api/system/last-restart': handle_get_last_restart,
    '/api/system/status': handle_get_system_status,
    '/api/workspaces': handle_get_workspaces,
    '/api/workspaces/select': handle_get_workspace_select,
    '/favicon.ico': handle_get_favicon,
    '/health': handle_get_health,
    '/index.html': handle_get_index,
    '/login': handle_get_login,
    '/m': handle_get_mobile,
    '/static/mobile.js': handle_get_mobile_js,
}

GET_PREFIX_ROUTES = [
    ('/static/', handle_serve_static),
    ('/assets/', handle_serve_assets),
    ('/preview/', handle_get_preview),
    ('/api/dynamic/status/', handle_get_dynamic_status),
    ('/api/plugins/', _handle_get_plugin_credentials_subpath),
]

# ── POST Raw Handlers (called BEFORE read_body) ──
POST_RAW_ROUTES = {
    '/api/whisper/transcribe': handle_post_whisper_transcribe,
}

# ── POST Parsed Handlers (receives (handler, parsed)) ──
POST_PARSED_ROUTES = {
    '/api/system/last-restart/ack': handle_post_last_restart_ack,
}

# ── O(1) POST Route Registry (receives (handler, body)) ──
POST_EXACT_ROUTES = {
    '/api/agent/message': handle_post_agent_message,
    '/api/approval/approve': handle_post_approval_approve,
    '/api/approval/reject': handle_post_approval_reject,
    '/api/approval/respond': handle_post_approval_respond,
    '/api/approval/skill-save/approve': handle_post_skill_save_approve,
    '/api/approval/skill-save/reject': handle_post_skill_save_reject,
    '/api/auth/login': handle_post_auth_login,
    '/api/auth/logout': handle_post_auth_logout,
    '/api/browser/back': handle_post_browser_back,
    '/api/browser/batch': handle_post_browser_batch,
    '/api/browser/click': handle_post_browser_click,
    '/api/browser/close': handle_post_browser_close,
    '/api/browser/close_tab': handle_post_browser_close_tab,
    '/api/browser/execute': handle_post_browser_execute,
    '/api/browser/focus': handle_post_browser_focus,
    '/api/browser/forward': handle_post_browser_forward,
    '/api/browser/navigate': handle_post_browser_navigate,
    '/api/browser/screenshot': handle_post_browser_screenshot,
    '/api/browser/snapshot': handle_post_browser_snapshot,
    '/api/browser/switch_tab': handle_post_browser_switch_tab,
    '/api/browser/sync_url': handle_post_browser_sync_url,
    '/api/browser/tabs': handle_post_browser_tabs,
    '/api/browser/type': handle_post_browser_type,
    '/api/capability/route': handle_post_capability_route,
    '/api/chat': handle_post_chat_sync,
    '/api/chat/cancel': handle_post_chat_cancel,
    '/api/chat/start': handle_post_chat_start,
    '/api/checkpoints/delete': handle_post_checkpoint_delete,
    '/api/checkpoints/rollback': handle_post_checkpoint_rollback,
    '/api/creative-director/brief': handle_post_creative_director_brief,
    '/api/creative-director/cards/extract': handle_post_creative_director_card_extract,
    '/api/crons/create': handle_post_cron_create,
    '/api/crons/delete': handle_post_cron_delete,
    '/api/crons/pause': handle_post_cron_pause,
    '/api/crons/resume': handle_post_cron_resume,
    '/api/crons/run': handle_post_cron_run,
    '/api/crons/update': handle_post_cron_update,
    '/api/debate/cancel': handle_post_debate_cancel,
    '/api/debate/next': handle_post_debate_next,
    '/api/debate/start': handle_post_debate_start,
    '/api/demo/add-event': handle_post_demo_add_event,
    '/api/demo/cancel': handle_post_demo_cancel,
    '/api/demo/skill/approve': handle_post_skill_approve,
    '/api/demo/skill/reject': handle_post_skill_reject,
    '/api/demo/start': handle_post_demo_start,
    '/api/demo/stop': handle_post_demo_stop,
    '/api/demo/text-workflow': handle_post_demo_text_workflow,
    '/api/docs/generate': handle_post_docs_generate,
    '/api/dynamic/run': handle_post_dynamic_run,
    '/api/file/apply-diff': handle_post_file_apply_diff,
    '/api/file/apply-preview': handle_post_file_apply_preview,
    '/api/file/create': handle_post_file_create,
    '/api/file/create-dir': handle_post_create_dir,
    '/api/file/delete': handle_post_file_delete,
    '/api/file/preview-diff': handle_post_file_preview_diff,
    '/api/file/reject-preview': handle_post_file_reject_preview,
    '/api/file/rename': handle_post_file_rename,
    '/api/file/save': handle_post_file_save,
    '/api/git/commit': handle_post_git_commit,
    '/api/git/discard': handle_post_git_discard,
    '/api/git/pull': handle_post_git_pull,
    '/api/git/push': handle_post_git_push,
    '/api/git/stage': handle_post_git_stage,
    '/api/git/unstage': handle_post_git_unstage,
    '/api/integration/config': handle_post_integration_config,
    '/api/integration/notion/create': handle_post_notion_create,
    '/api/integration/notion/test': handle_post_notion_test,
    '/api/integration/slack/send': handle_post_slack_send,
    '/api/integration/slack/test': handle_post_slack_test,
    '/api/kakao/send': handle_post_kakao_send,
    '/api/mcp/exchange-ott': handle_post_mcp_exchange_ott,
    '/api/mcp/servers/add': handle_post_mcp_server_add,
    '/api/mcp/servers/add-preset': handle_post_mcp_server_add_preset,
    '/api/mcp/servers/connect': handle_post_mcp_server_connect,
    '/api/mcp/servers/disconnect': handle_post_mcp_server_disconnect,
    '/api/mcp/servers/remove': handle_post_mcp_server_remove,
    '/api/mcp/tools/call': handle_post_mcp_tool_call,
    '/api/memory/fact/delete': handle_post_memory_fact_delete,
    '/api/memory/profile/set': handle_post_memory_profile_set,
    '/api/memory/review/resolve': handle_post_memory_review_resolve,
    '/api/memory/write': handle_post_memory_write,
    '/api/mode': handle_post_mode,
    '/api/mode/intent': handle_post_mode_intent,
    '/api/patches/delete': handle_post_patch_delete,
    '/api/patches/register': handle_post_patch_register,
    '/api/patches/seed': handle_post_patch_seed,
    '/api/patches/update': handle_post_patch_update,
    '/api/plugins/import': handle_post_plugins_import,
    '/api/profile/create': handle_post_profile_create,
    '/api/profile/delete': handle_post_profile_delete,
    '/api/profile/switch': handle_post_profile_switch,
    '/api/projects/create': handle_post_project_create,
    '/api/projects/delete': handle_post_project_delete,
    '/api/projects/rename': handle_post_project_rename,
    '/api/providers/add': handle_post_provider_add,
    '/api/providers/delete': handle_post_provider_delete,
    '/api/providers/fetch-models': handle_post_provider_fetch_models,
    '/api/providers/refresh-models': handle_post_provider_refresh_models,
    '/api/providers/update-models': handle_post_provider_update_models,
    '/api/session/archive': handle_post_session_archive,
    '/api/session/clear': handle_post_session_clear,
    '/api/session/delete': handle_post_session_delete,
    '/api/session/import': handle_post_session_import,
    '/api/session/import_cli': handle_post_session_import_cli,
    '/api/session/move': handle_post_session_move,
    '/api/session/new': handle_post_session_new,
    '/api/session/pin': handle_post_session_pin,
    '/api/session/rename': handle_post_session_rename,
    '/api/session/truncate': handle_post_session_truncate,
    '/api/session/update': handle_post_session_update,
    '/api/sessions/cleanup': handle_post_sessions_cleanup,
    '/api/sessions/cleanup_zero_message': handle_post_sessions_cleanup,
    '/api/dynamic/cancel': lambda h, b: handle_post_dynamic_cancel(h, b),
    '/api/dynamic/run': handle_post_dynamic_run,
    '/api/settings': handle_post_settings,
    '/api/setup/generate': handle_post_setup_generate,
    '/api/skills/delete': handle_post_skill_delete,
    '/api/skills/from-github': handle_post_skills_from_github,
    '/api/skills/install': handle_post_skills_hub_install,
    '/api/skills/promote': handle_post_skill_promote,
    '/api/skills/reject': handle_post_skill_reject,
    '/api/skills/save': handle_post_skill_save,
    '/api/style-cards/delete': handle_post_style_card_delete,
    '/api/style-cards/extract': handle_post_style_card_extract,
    '/api/style-cards/rebuild-index': handle_post_style_cards_rebuild_index,
    '/api/style-cards/save': handle_post_style_card_save,
    '/api/sync/hook/install': handle_post_sync_hook_install,
    '/api/sync/hook/uninstall': handle_post_sync_hook_uninstall,
    '/api/sync/start': handle_post_sync_start,
    '/api/sync/stop': handle_post_sync_stop,
    '/api/upload': handle_post_upload,
    '/api/workspaces/add': handle_post_workspace_add,
    '/api/workspaces/remove': handle_post_workspace_remove,
    '/api/workspaces/rename': handle_post_workspace_rename,
}

POST_PREFIX_ROUTES = [
    ('/api/dynamic/approve/', lambda h, b, p: handle_post_dynamic_approve(h, b, p)),
    ('/api/dynamic/answer/', lambda h, b, p: handle_post_dynamic_answer(h, b, p)),
    ('/api/dynamic/cancel/', lambda h, b, p: handle_post_dynamic_cancel(h, b, p)),
    ('/api/plugins/', _handle_post_plugin_subpath),
]


# ── Core Dispatchers ──

def handle_get(handler, parsed) -> bool:
    """Handle all GET routes via O(1) exact lookup or directory-prefix match."""
    path = parsed.path
    
    # 1. Exact match (O(1))
    func = GET_EXACT_ROUTES.get(path)
    if func is not None:
        return func(handler, parsed)
        
    # 2. Directory prefix match (strictly boundary-delimited)
    for prefix, pfunc in GET_PREFIX_ROUTES:
        if path == prefix.rstrip('/') or path.startswith(prefix):
            return pfunc(handler, parsed)
            
    _logger.debug("No GET route matched for: %s", path)
    return False


def handle_post(handler, parsed) -> bool:
    """Handle all POST routes via O(1) exact lookup or directory-prefix match."""
    path = parsed.path
    
    # 1. Raw endpoint (before reading JSON body)
    raw_func = POST_RAW_ROUTES.get(path)
    if raw_func is not None:
        return raw_func(handler, parsed)
        
    # 2. Read request body
    body = read_body(handler)
    handler.body = body
    
    # 3. Exact match with parsed signature (handler, parsed)
    parsed_func = POST_PARSED_ROUTES.get(path)
    if parsed_func is not None:
        return parsed_func(handler, parsed)
        
    # 4. Exact match with standard signature (handler, body) (O(1))
    func = POST_EXACT_ROUTES.get(path)
    if func is not None:
        return func(handler, body)
        
    # 5. Directory prefix match (strictly boundary-delimited)
    for prefix, pfunc in POST_PREFIX_ROUTES:
        if path == prefix.rstrip('/') or path.startswith(prefix):
            return pfunc(handler, body, parsed)
            
    _logger.debug("No POST route matched for: %s", path)
    return False

