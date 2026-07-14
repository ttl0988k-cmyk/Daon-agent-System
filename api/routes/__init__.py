"""
Hermes Web UI — Route handler registry.
Aggregates all GET/POST route handlers from sub-modules.
"""
import logging
from urllib.parse import parse_qs

from api.helpers import j, bad, read_body

_logger = logging.getLogger(__name__)
from api.config import load_settings

from api.routes.admin_routes import (
    _LOGIN_PAGE_HTML,
    handle_get_index,
    handle_get_login,
    handle_get_auth_status,
    handle_get_favicon,
    handle_get_health,
    handle_get_models,
    handle_serve_static,
    handle_get_approval_pending,
    handle_get_approval_inject,
    handle_get_crons,
    handle_get_cron_output,
    handle_get_cron_recent,
    handle_get_skills,
    handle_get_skill_content,
    handle_get_memory,
    handle_post_approval_respond,
    handle_post_skill_promote,
    handle_post_skill_reject,
    handle_post_skill_save,
    handle_post_skill_delete,
    handle_post_memory_write,
    handle_post_cron_create,
    handle_post_cron_update,
    handle_post_cron_delete,
    handle_post_cron_run,
    handle_post_cron_pause,
    handle_post_cron_resume,
    handle_post_auth_login,
    handle_post_auth_logout,
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
)
from api.routes.dashboard_routes import (
    handle_get_dashboard_metrics,
)
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
from api.routes.whisper_routes import (
    handle_post_whisper_transcribe,
)
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
    handle_post_browser_navigate,
    handle_post_browser_sync_url,
    handle_post_browser_snapshot,
    handle_post_browser_click,
    handle_post_browser_type,
    handle_post_browser_screenshot,
    handle_post_browser_execute,
    handle_post_browser_close,
    handle_get_browser_recommend,
)
from api.routes.mcp_routes import (
    handle_get_mcp_servers,
    handle_get_mcp_presets,
    handle_post_mcp_server_add,
    handle_post_mcp_server_remove,
    handle_post_mcp_server_connect,
    handle_post_mcp_server_disconnect,
    handle_post_mcp_server_add_preset,
    handle_post_mcp_tool_call,
)
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
from api.routes.skills_hub_routes import (
    handle_get_skills_hub_search,
    handle_get_skills_hub_sources,
    handle_get_skills_hub_recommend,
    handle_post_skills_hub_install,
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
from api.routes.kakao_routes import (
    handle_get_kakao_status,
    handle_post_kakao_send,
)

def handle_get(handler, parsed) -> bool:
    """Handle all GET routes. Returns True if handled, False for 404."""
    import sys
    print(f"[DEBUG handle_get] path={repr(parsed.path)}", file=sys.stderr, flush=True)

    if parsed.path in ('/', '/index.html'):
        return handle_get_index(handler, parsed)

    if parsed.path == '/login':
        return handle_get_login(handler, parsed)

    if parsed.path == '/api/auth/status':
        return handle_get_auth_status(handler, parsed)

    if parsed.path == '/favicon.ico':
        return handle_get_favicon(handler, parsed)

    if parsed.path == '/health':
        return handle_get_health(handler, parsed)

    if parsed.path == '/api/models':
        return handle_get_models(handler, parsed)

    if parsed.path == '/api/providers':
        return handle_get_providers(handler, parsed)

    if parsed.path == '/api/settings':
        return handle_get_settings(handler, parsed)

    if parsed.path.startswith('/static/'):
        return handle_serve_static(handler, parsed)

    if parsed.path == '/api/session':
        return handle_get_session(handler, parsed)

    if parsed.path == '/api/sessions':
        return handle_get_sessions(handler, parsed)

    if parsed.path == '/api/projects':
        return handle_get_projects(handler, parsed)

    if parsed.path == '/api/session/export':
        return handle_get_session_export(handler, parsed)

    if parsed.path == '/api/workspaces':
        return handle_get_workspaces(handler, parsed)

    if parsed.path == '/api/sessions/search':
        return handle_get_sessions_search(handler, parsed)

    if parsed.path == '/api/list':
        return handle_get_list_dir(handler, parsed)

    if parsed.path == '/api/git-info':
        return handle_get_git_info(handler, parsed)

    # ── Git Automation API (GET) ──
    if parsed.path == '/api/git/status':
        return handle_get_git_status(handler, parsed)

    if parsed.path == '/api/git/diff':
        return handle_get_git_diff(handler, parsed)

    if parsed.path == '/api/git/log':
        return handle_get_git_log(handler, parsed)

    if parsed.path == '/api/git/conflicts':
        return handle_get_git_conflict(handler, parsed)

    if parsed.path == '/api/chat/stream/status':
        return handle_get_stream_status(handler, parsed)

    if parsed.path == '/api/chat/cancel':
        return handle_get_chat_cancel(handler, parsed)

    if parsed.path == '/api/chat/stream':
        return handle_get_sse_stream(handler, parsed)

    if parsed.path == '/api/file/raw':
        return handle_get_file_raw(handler, parsed)

    if parsed.path == '/api/file':
        return handle_get_file_read(handler, parsed)

    if parsed.path == '/api/approval/pending':
        return handle_get_approval_pending(handler, parsed)

    if parsed.path == '/api/approval/inject_test':
        if handler.client_address[0] != '127.0.0.1':
            return j(handler, {'error': 'not found'}, status=404)
        return handle_get_approval_inject(handler, parsed)

    # ── Cron API (GET) ──
    if parsed.path == '/api/crons':
        return handle_get_crons(handler, parsed)

    if parsed.path == '/api/crons/output':
        return handle_get_cron_output(handler, parsed)

    if parsed.path == '/api/crons/recent':
        return handle_get_cron_recent(handler, parsed)

    # ── Skills API (GET) ──
    if parsed.path == '/api/skills':
        return handle_get_skills(handler, parsed)

    if parsed.path == '/api/skills/content':
        return handle_get_skill_content(handler, parsed)

    # ── Memory API (GET) ──
    if parsed.path == '/api/memory':
        return handle_get_memory(handler, parsed)

    # ── Profile API (GET) ──
    if parsed.path == '/api/profiles':
        return handle_get_profiles(handler, parsed)

    if parsed.path == '/api/profile/active':
        return handle_get_profile_active(handler, parsed)

    # ── Dashboard API (GET) ──
    if parsed.path == '/api/dashboard/metrics':
        return handle_get_dashboard_metrics(handler, parsed)

    # ── Documentation API (GET) ──
    if parsed.path == '/api/docs/status':
        return handle_get_docs_status(handler, parsed)

    if parsed.path == '/api/docs/list':
        return handle_get_docs_list(handler, parsed)

    # ── Integration API (GET) ──
    if parsed.path == '/api/integration/config':
        return handle_get_integration_config(handler, parsed)

    # ── Checkpoint API (GET) ──
    if parsed.path == '/api/checkpoints':
        return handle_get_checkpoints(handler, parsed)

    # ── Diff Preview & History API (GET) ──
    if parsed.path == '/api/diff/history':
        return handle_get_diff_history(handler, parsed)

    if parsed.path == '/api/diff/preview':
        return handle_get_diff_preview(handler, parsed)

    # ── Mode API (GET) ──
    if parsed.path == '/api/modes':
        return handle_get_modes(handler, parsed)

    if parsed.path == '/api/mode':
        return handle_get_mode(handler, parsed)

    # ── Approval API (GET) ──
    if parsed.path == '/api/approval/pending':
        return handle_get_approval_pending(handler, parsed)

    if parsed.path == '/api/approval/history':
        return handle_get_approval_history(handler, parsed)

    # ── Browser Automation API (GET) ──
    if parsed.path == '/api/browser/status':
        _logger.debug("Browser status route matched, calling handle_get_browser_status")
        result = handle_get_browser_status(handler, parsed)
        _logger.debug("handle_get_browser_status returned: %s", result)
        return result

    if parsed.path == '/api/browser/recommend':
        return handle_get_browser_recommend(handler, parsed)

    # ── Demo-to-Skill API (GET) ──
    if parsed.path == '/api/demo/status':
        return handle_get_demo_status(handler, parsed)

    if parsed.path == '/api/demo/events':
        return handle_get_demo_events(handler, parsed)

    # ── MCP API (GET) ──
    if parsed.path == '/api/mcp/servers':
        return handle_get_mcp_servers(handler, parsed)

    if parsed.path == '/api/mcp/presets':
        return handle_get_mcp_presets(handler, parsed)

    # ── Sync API (GET) ──
    if parsed.path == '/api/sync/status':
        return handle_get_sync_status(handler, parsed)

    # ── Skills Hub API (GET) ──
    if parsed.path.startswith('/api/skills/search'):
        return handle_get_skills_hub_search(handler, parsed)

    if parsed.path.startswith('/api/skills/recommend'):
        return handle_get_skills_hub_recommend(handler, parsed)

    if parsed.path == '/api/skills/hub/sources':
        return handle_get_skills_hub_sources(handler, parsed)

    # ── Style Cards API (GET) ──
    if parsed.path == '/api/style-cards':
        return handle_get_style_cards(handler, parsed)

    if parsed.path == '/api/style-cards/content':
        return handle_get_style_card_content(handler, parsed)

    if parsed.path == '/api/style-cards/categories':
        return handle_get_style_cards_categories(handler, parsed)

    # ── KakaoTalk Bridge API (GET) ──
    if parsed.path == '/api/kakao/status':
        return handle_get_kakao_status(handler, parsed)

    _logger.debug("No GET route matched for: %s", parsed.path)
    return False  # 404


def handle_post(handler, parsed) -> bool:
    """Handle all POST routes. Returns True if handled, False for 404."""

    if parsed.path == '/api/upload':
        return handle_post_upload(handler, parsed)

    body = read_body(handler)
    handler.body = body

    if parsed.path == '/api/session/new':
        return handle_post_session_new(handler, body)

    if parsed.path == '/api/sessions/cleanup':
        return handle_post_sessions_cleanup(handler, body, zero_only=False)

    if parsed.path == '/api/sessions/cleanup_zero_message':
        return handle_post_sessions_cleanup(handler, body, zero_only=True)

    if parsed.path == '/api/session/rename':
        return handle_post_session_rename(handler, body)

    if parsed.path == '/api/session/update':
        return handle_post_session_update(handler, body)

    if parsed.path == '/api/session/delete':
        return handle_post_session_delete(handler, body)

    if parsed.path == '/api/session/clear':
        return handle_post_session_clear(handler, body)

    if parsed.path == '/api/session/truncate':
        return handle_post_session_truncate(handler, body)

    if parsed.path == '/api/chat/start':
        return handle_post_chat_start(handler, body)

    if parsed.path == '/api/chat':
        return handle_post_chat_sync(handler, body)

    # ── Cron API (POST) ──
    if parsed.path == '/api/crons/create':
        return handle_post_cron_create(handler, body)

    if parsed.path == '/api/crons/update':
        return handle_post_cron_update(handler, body)

    if parsed.path == '/api/crons/delete':
        return handle_post_cron_delete(handler, body)

    if parsed.path == '/api/crons/run':
        return handle_post_cron_run(handler, body)

    if parsed.path == '/api/crons/pause':
        return handle_post_cron_pause(handler, body)

    if parsed.path == '/api/crons/resume':
        return handle_post_cron_resume(handler, body)

    # ── File ops (POST) ──
    if parsed.path == '/api/file/delete':
        return handle_post_file_delete(handler, body)

    if parsed.path == '/api/file/save':
        return handle_post_file_save(handler, body)

    if parsed.path == '/api/file/create':
        return handle_post_file_create(handler, body)

    if parsed.path == '/api/file/rename':
        return handle_post_file_rename(handler, body)

    if parsed.path == '/api/file/create-dir':
        return handle_post_create_dir(handler, body)

    # ── Workspace management (POST) ──
    if parsed.path == '/api/workspaces/add':
        return handle_post_workspace_add(handler, body)

    if parsed.path == '/api/workspaces/remove':
        return handle_post_workspace_remove(handler, body)

    if parsed.path == '/api/workspaces/rename':
        return handle_post_workspace_rename(handler, body)

    # ── Git Automation API (POST) ──
    if parsed.path == '/api/git/commit':
        return handle_post_git_commit(handler, body)

    if parsed.path == '/api/git/push':
        return handle_post_git_push(handler, body)

    if parsed.path == '/api/git/pull':
        return handle_post_git_pull(handler, body)

    if parsed.path == '/api/git/stage':
        return handle_post_git_stage(handler, body)

    if parsed.path == '/api/git/unstage':
        return handle_post_git_unstage(handler, body)

    if parsed.path == '/api/git/discard':
        return handle_post_git_discard(handler, body)

    # ── Approval (POST) ──
    if parsed.path == '/api/approval/respond':
        return handle_post_approval_respond(handler, body)

    # ── Skills (POST) ──
    if parsed.path == '/api/skills/promote':
        return handle_post_skill_promote(handler, body)

    if parsed.path == '/api/skills/reject':
        return handle_post_skill_reject(handler, body)

    if parsed.path == '/api/skills/save':
        return handle_post_skill_save(handler, body)

    if parsed.path == '/api/skills/delete':
        return handle_post_skill_delete(handler, body)

    # ── Memory (POST) ──
    if parsed.path == '/api/memory/write':
        return handle_post_memory_write(handler, body)

    # ── Profile API (POST) ──
    if parsed.path == '/api/profile/switch':
        return handle_post_profile_switch(handler, body)

    if parsed.path == '/api/profile/create':
        return handle_post_profile_create(handler, body)

    if parsed.path == '/api/profile/delete':
        return handle_post_profile_delete(handler, body)

    # ── Custom Providers (POST) ──
    if parsed.path == '/api/providers/add':
        return handle_post_provider_add(handler, body)

    if parsed.path == '/api/providers/delete':
        return handle_post_provider_delete(handler, body)

    if parsed.path == '/api/providers/fetch-models':
        return handle_post_provider_fetch_models(handler, body)

    if parsed.path == '/api/providers/update-models':
        return handle_post_provider_update_models(handler, body)

    # ── Settings (POST) ──
    if parsed.path == '/api/settings':
        return handle_post_settings(handler, body)

    # ── Session pin (POST) ──
    if parsed.path == '/api/session/pin':
        return handle_post_session_pin(handler, body)

    # ── Session archive (POST) ──
    if parsed.path == '/api/session/archive':
        return handle_post_session_archive(handler, body)

    # ── Session move to project (POST) ──
    if parsed.path == '/api/session/move':
        return handle_post_session_move(handler, body)

    # ── Project CRUD (POST) ──
    if parsed.path == '/api/projects/create':
        return handle_post_project_create(handler, body)

    if parsed.path == '/api/projects/rename':
        return handle_post_project_rename(handler, body)

    if parsed.path == '/api/projects/delete':
        return handle_post_project_delete(handler, body)

    # ── Session import from JSON (POST) ──
    if parsed.path == '/api/session/import':
        return handle_post_session_import(handler, body)

    # ── CLI session import (POST) ──
    if parsed.path == '/api/session/import_cli':
        return handle_post_session_import_cli(handler, body)

    # ── Auth endpoints (POST) ──
    if parsed.path == '/api/auth/login':
        return handle_post_auth_login(handler, body)

    if parsed.path == '/api/auth/logout':
        return handle_post_auth_logout(handler, body)

    if parsed.path == '/api/whisper/transcribe':
        return handle_post_whisper_transcribe(handler, parsed)

    # ── Browser Automation API (POST) ──
    if parsed.path == '/api/browser/navigate':
        return handle_post_browser_navigate(handler, body)

    if parsed.path == '/api/browser/sync_url':
        return handle_post_browser_sync_url(handler, body)

    if parsed.path == '/api/browser/snapshot':
        return handle_post_browser_snapshot(handler, body)

    if parsed.path == '/api/browser/click':
        return handle_post_browser_click(handler, body)

    if parsed.path == '/api/browser/type':
        return handle_post_browser_type(handler, body)

    if parsed.path == '/api/browser/screenshot':
        return handle_post_browser_screenshot(handler, body)

    if parsed.path == '/api/browser/execute':
        return handle_post_browser_execute(handler, body)

    if parsed.path == '/api/browser/close':
        return handle_post_browser_close(handler, body)

    # ── Documentation API (POST) ──
    if parsed.path == '/api/docs/generate':
        return handle_post_docs_generate(handler, body)

    # ── Integration API (POST) ──
    if parsed.path == '/api/integration/config':
        return handle_post_integration_config(handler, body)

    if parsed.path == '/api/integration/slack/send':
        return handle_post_slack_send(handler, body)

    if parsed.path == '/api/integration/slack/test':
        return handle_post_slack_test(handler, body)

    if parsed.path == '/api/integration/notion/create':
        return handle_post_notion_create(handler, body)

    if parsed.path == '/api/integration/notion/test':
        return handle_post_notion_test(handler, body)

    # ── Diff & Checkpoint API (POST) ──
    if parsed.path == '/api/file/preview-diff':
        return handle_post_file_preview_diff(handler, body)

    if parsed.path == '/api/file/apply-diff':
        return handle_post_file_apply_diff(handler, body)

    if parsed.path == '/api/file/apply-preview':
        return handle_post_file_apply_preview(handler, body)

    if parsed.path == '/api/file/reject-preview':
        return handle_post_file_reject_preview(handler, body)

    if parsed.path == '/api/checkpoints/rollback':
        return handle_post_checkpoint_rollback(handler, body)

    if parsed.path == '/api/checkpoints/delete':
        return handle_post_checkpoint_delete(handler, body)

    # ── Mode API (POST) ──
    if parsed.path == '/api/mode/intent':
        return handle_post_mode_intent(handler, body)

    if parsed.path == '/api/mode':
        return handle_post_mode(handler, body)

    # ── MCP API (POST) ──
    if parsed.path == '/api/mcp/servers/add':
        return handle_post_mcp_server_add(handler, body)

    if parsed.path == '/api/mcp/servers/remove':
        return handle_post_mcp_server_remove(handler, body)

    if parsed.path == '/api/mcp/servers/connect':
        return handle_post_mcp_server_connect(handler, body)

    if parsed.path == '/api/mcp/servers/disconnect':
        return handle_post_mcp_server_disconnect(handler, body)

    if parsed.path == '/api/mcp/servers/add-preset':
        return handle_post_mcp_server_add_preset(handler, body)

    if parsed.path == '/api/mcp/tools/call':
        return handle_post_mcp_tool_call(handler, body)

    # ── Approval API (POST) ──
    if parsed.path == '/api/approval/approve':
        return handle_post_approval_approve(handler, body)

    if parsed.path == '/api/approval/reject':
        return handle_post_approval_reject(handler, body)

    if parsed.path == '/api/approval/skill-save/approve':
        return handle_post_skill_save_approve(handler, body)

    if parsed.path == '/api/approval/skill-save/reject':
        return handle_post_skill_save_reject(handler, body)

    # ── Demo-to-Skill API (POST) ──
    if parsed.path == '/api/demo/start':
        return handle_post_demo_start(handler, body)

    if parsed.path == '/api/demo/stop':
        return handle_post_demo_stop(handler, body)

    if parsed.path == '/api/demo/cancel':
        return handle_post_demo_cancel(handler, body)

    if parsed.path == '/api/demo/text-workflow':
        return handle_post_demo_text_workflow(handler, body)

    if parsed.path == '/api/demo/add-event':
        return handle_post_demo_add_event(handler, body)

    if parsed.path == '/api/demo/skill/approve':
        return handle_post_skill_approve(handler, body)

    if parsed.path == '/api/demo/skill/reject':
        return handle_post_skill_reject(handler, body)

    # ── Debate API (POST) ──
    if parsed.path == '/api/debate/start':
        return handle_post_debate_start(handler, body)

    if parsed.path == '/api/debate/next':
        return handle_post_debate_next(handler, body)

    if parsed.path == '/api/debate/cancel':
        return handle_post_debate_cancel(handler, body)

    # ── Sync API (POST) ──
    if parsed.path == '/api/sync/start':
        return handle_post_sync_start(handler, body)

    if parsed.path == '/api/sync/stop':
        return handle_post_sync_stop(handler, body)

    if parsed.path == '/api/sync/hook/install':
        return handle_post_sync_hook_install(handler, body)

    if parsed.path == '/api/sync/hook/uninstall':
        return handle_post_sync_hook_uninstall(handler, body)

    # ── Skills Hub API (POST) ──
    if parsed.path == '/api/skills/install':
        return handle_post_skills_hub_install(handler, body)

    # ── Style Cards API (POST) ──
    if parsed.path == '/api/style-cards/extract':
        return handle_post_style_card_extract(handler, body)

    if parsed.path == '/api/style-cards/save':
        return handle_post_style_card_save(handler, body)

    if parsed.path == '/api/style-cards/delete':
        return handle_post_style_card_delete(handler, body)

    if parsed.path == '/api/style-cards/rebuild-index':
        return handle_post_style_cards_rebuild_index(handler, body)

    # ── KakaoTalk Bridge API (POST) ──
    if parsed.path == '/api/kakao/send':
        return handle_post_kakao_send(handler, body)

    return False  # 404

