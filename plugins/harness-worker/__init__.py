"""harness-worker 플러그인 - 외부 코딩 하네스(Codex / Claude Code)를 워커로 부린다.

코어 무수정. `PluginContext.register_tool()` 로 도구 3개를 등록한다.

  dispatch_worker  : codex | claude 워커를 실제 실행하고 정제된 결과 반환
  worker_status    : 두 하네스 + 게이트웨이 + 프로바이더 키 상태 일괄 조회
  worker_gateway   : LiteLLM 게이트웨이 기동/종료/상태 (claude 계열에 필요)

등록 toolset 은 "harness_worker" 다.
⚠️ 노출은 프로필 config.yaml 의 toolsets.default 에
   "harness_worker" 를 넣은 프로필에서만 일어난다. 넣지 않으면 로드만 되고
   모델에게는 보이지 않는다 - 의도된 안전장치다.

[2026-09-20 프로바이더 선택식]
  provider 인자로 LLM 백엔드를 고른다:
    openrouter(기본) | opencode-go | qwen-token-plan | minimax | omniroute
  · Codex  : 프로바이더별 임시 CODEX_HOME 을 만들어 주입한다.
             사용자의 실제 ~/.codex 는 건드리지 않는다.
  · Claude : 게이트웨이가 모든 프로바이더 alias 를 한 번에 노출한다
             (claude-openrouter / claude-oc / claude-qwen / claude-minimax / claude-omni).
             한 번 띄우면 프로바이더를 바꿔도 재기동이 필요 없다.

이 플러그인이 코드로 봉인하는 것 (사람이 잊으면 재발하는 것들):
  - 비대화형 실행 시 stdin 을 닫지 않아 CLI 가 멈추는 문제
  - Codex 가 git 저장소 밖에서 거부하는 문제
  - Codex 0.152+ 의 wire_api="chat" 폐지 (→ "responses" 강제)
  - Codex 가 전역 config.toml 을 읽어 프로바이더 전환이 안 되는 문제
  - opencode-go 가 헤더 없으면 403/400 으로 죽는 문제
  - warning/tokens used 잡음이 응답에 섞이는 문제

되돌리기
  plugin_toggle(name="harness-worker", enabled=false) - 즉시 무효화.
  도구는 registry 에서 빠지고 프로세스 상태도 남지 않는다.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
from typing import Any, Dict

from . import worker

# 재디스커버(force) 때 이 파일은 다시 실행되지만 서브모듈 worker 는
# sys.modules 캐시에 남아  코드가 계속 쓰인다 (_unload_plugins 는
# sys.modules 를 정리하지 않는다). 명시적 reload 로 매번 디스크에서
# 다시 읽어, worker.py 수정이 재기동 없이 반영되게 한다.
try:
    importlib.reload(worker)
except Exception:  # noqa: BLE001 - 로드 실패 시 기존 모듈로 계속 동작
    pass

logger = logging.getLogger(__name__)

TOOLSET = "harness_worker"


# ---------------------------------------------------------------------------
# 스키마
# ---------------------------------------------------------------------------

DISPATCH_SCHEMA: Dict[str, Any] = {
    "name": "dispatch_worker",
    "description": (
        "Spawn an external coding harness (Codex CLI or Claude Code) as a worker. "
        "By default runs as a non-blocking background job (background=true) and immediately "
        "returns a job_id so you can notify the user and continue conversing without freezing. "
        "Use worker_job(action='status', job_id='...') to check progress or retrieve results. "
        "Set background=false if you explicitly need to wait and block for the final result synchronously. "
        "harness='codex' runs the Codex CLI; harness='claude' runs Claude Code. "
        "provider selects the LLM backend: 'openrouter' (default), 'opencode-go', "
        "'qwen-token-plan', 'minimax', or 'omniroute'. Codex gets an isolated CODEX_HOME "
        "per provider (your ~/.codex is never touched). Claude Code routes through LiteLLM. "
        "The worker runs in an isolated git repository by default; pass isolate=false with "
        "workdir to operate on an existing project directory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "harness": {
                "type": "string",
                "enum": ["codex", "claude"],
                "description": "Which external harness to spawn.",
            },
            "provider": {
                "type": "string",
                "enum": ["openrouter", "opencode-go", "qwen-token-plan",
                         "minimax", "omniroute"],
                "description": (
                    "LLM backend for the worker. Defaults to 'openrouter'. "
                    "Short aliases also work: oc=opencode-go, qwen=qwen-token-plan, "
                    "mm=minimax, omni=omniroute."
                ),
            },
            "prompt": {
                "type": "string",
                "description": "The task instruction handed to the worker, verbatim.",
            },
            "workdir": {
                "type": "string",
                "description": (
                    "Directory for the worker to operate in. Omit for a throwaway "
                    "isolated git repo under C:\\daon\\_worker_runs."
                ),
            },
            "isolate": {
                "type": "boolean",
                "description": (
                    "true (default when workdir is omitted): fresh temporary git repo, "
                    "removed afterwards unless keep=true. "
                    "false: use workdir as-is (a git repo is initialized if missing)."
                ),
            },
            "model": {
                "type": "string",
                "description": (
                    "Optional model override for the worker. "
                    "Codex example: 'qwen3.8-max' (Qwen) or 'deepseek-v4.1-flash' (opencode-go). "
                    "Claude must use a LiteLLM alias: 'claude-qwen', 'claude-oc', "
                    "'claude-minimax', 'claude-omni', 'claude-openrouter'."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "Hard timeout in seconds (default 600).",
            },
            "background": {
                "type": "boolean",
                "description": (
                    "true (default): Run as a non-blocking background job and return job_id immediately, "
                    "so you can inform the user and continue conversing without blocking. "
                    "false: Block and wait synchronously for the final result."
                ),
            },
            "full_auto": {
                "type": "boolean",
                "description": (
                    "true (default): Bypass sandbox/terminal approval prompts for Codex and Claude Code "
                    "so the worker can write files in headless mode without freezing."
                ),
            },
            "keep": {
                "type": "boolean",
                "description": "Keep the isolated workdir after the run for inspection.",
            },
        },
        "required": ["harness", "prompt"],
    },
}

JOB_SCHEMA: Dict[str, Any] = {
    "name": "worker_job",
    "description": (
        "Inspect, list, or terminate external background coding worker jobs (Codex / Claude Code). "
        "Use action='status' with job_id to check if a worker has finished, read its output, or inspect errors. "
        "Use action='list' to see all recent worker jobs and their current statuses (running/completed/failed). "
        "Use action='kill' with job_id to abort a running worker."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "list", "kill"],
                "description": "Action to perform: 'status', 'list', or 'kill'.",
            },
            "job_id": {
                "type": "string",
                "description": "The job ID to inspect or kill (required for 'status' and 'kill').",
            },
        },
        "required": ["action"],
    },
}

STATUS_SCHEMA: Dict[str, Any] = {
    "name": "worker_status",
    "description": (
        "Report readiness of the external coding harnesses: OpenRouter key presence, "
        "Codex and Claude Code binaries, LiteLLM gateway health and PID, and recent "
        "worker run directories. Call this before dispatching to confirm a harness is usable."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

GATEWAY_SCHEMA: Dict[str, Any] = {
    "name": "worker_gateway",
    "description": (
        "Control the LiteLLM gateway that Claude Code needs (Codex does not need it). "
        "action='up' starts it in the background and waits for health, 'down' stops it "
        "by PID, 'status' reports health/PID/port/config. "
        "Claude Code rejects unrecognized model names, so the gateway exposes claude-* "
        "aliases for EVERY provider at once: claude-openrouter, claude-oc (opencode-go), "
        "claude-qwen, claude-minimax, claude-omni. Start it once, then switch providers "
        "with the 'model' argument - no restart needed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["up", "down", "status"],
                "description": "Gateway operation to perform.",
            },
        },
        "required": ["action"],
    },
}


# ---------------------------------------------------------------------------
# 핸들러
# ---------------------------------------------------------------------------

def _json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _dispatch(args: Dict[str, Any], **_: Any) -> str:
    try:
        importlib.reload(worker)
    except Exception:
        pass

    harness = str(args.get("harness") or "").strip().lower()
    prompt = str(args.get("prompt") or "").strip()

    if harness not in ("codex", "claude", "claude-code"):
        return _json({"ok": False,
                      "error": "harness must be 'codex' or 'claude'.",
                      "received": harness})
    if not prompt:
        return _json({"ok": False, "error": "prompt is required."})

    # workdir 미지정 시 격리 기본값
    workdir = args.get("workdir")
    isolate = args.get("isolate")
    if isolate is None:
        isolate = not bool(workdir)
    else:
        isolate = bool(isolate)

    try:
        timeout = int(args.get("timeout") or 600)
    except (TypeError, ValueError):
        timeout = 600
    timeout = max(10, min(timeout, 1800))

    full_auto = args.get("full_auto")
    if full_auto is None:
        full_auto = True
    else:
        full_auto = bool(full_auto)

    background = args.get("background")
    if background is None:
        background = True
    else:
        background = bool(background)

    session_id = os.environ.get("HERMES_SESSION_KEY") or None

    with_mcp = bool(args.get("with_mcp", False))

    if background:
        try:
            res = worker.start_background_job(
                harness=harness,
                prompt=prompt,
                workdir=workdir,
                isolate=isolate,
                model=args.get("model") or None,
                timeout=timeout,
                full_auto=full_auto,
                keep=bool(args.get("keep")),
                provider=args.get("provider") or None,
                session_id=session_id,
                with_mcp=with_mcp,
            )
        except Exception as exc:
            logger.exception("harness-worker: background dispatch failed")
            return _json({"ok": False, "error": f"internal error: {exc}", "harness": harness})

        logger.info(
            "harness-worker: dispatched background job=%s harness=%s provider=%s workdir=%s",
            res.get("job_id"), res.get("harness"), res.get("provider"), res.get("workdir"),
        )
        return _json(res)

    try:
        res = worker.run_worker(
            harness=harness,
            prompt=prompt,
            workdir=workdir,
            isolate=isolate,
            model=args.get("model") or None,
            timeout=timeout,
            full_auto=full_auto,
            keep=bool(args.get("keep")),
            provider=args.get("provider") or None,
            with_mcp=with_mcp,
        )
    except Exception as exc:  # 도구는 절대 예외를 밖으로 던지지 않는다
        logger.exception("harness-worker: dispatch failed")
        return _json({"ok": False, "error": f"internal error: {exc}", "harness": harness})

    logger.info(
        "harness-worker: dispatch harness=%s provider=%s ok=%s exit=%s elapsed=%ss workdir=%s",
        res.get("harness"), res.get("provider"), res.get("ok"), res.get("exit_code"),
        res.get("elapsed"), res.get("workdir"),
    )
    return _json(res)


def _worker_job(args: Dict[str, Any], **_: Any) -> str:
    try:
        importlib.reload(worker)
    except Exception:
        pass

    action = str(args.get("action") or "list").strip().lower()
    job_id = str(args.get("job_id") or "").strip()

    if action == "status":
        if not job_id:
            return _json({"ok": False, "error": "job_id is required for action='status'"})
        job = worker.get_job(job_id)
        if not job:
            return _json({"ok": False, "error": f"Job not found: {job_id}"})
        return _json({"ok": True, "job": job})

    elif action == "list":
        session_id = os.environ.get("HERMES_SESSION_KEY") or None
        jobs = worker.list_jobs(session_id=session_id)
        return _json({"ok": True, "jobs": jobs, "count": len(jobs)})

    elif action == "kill":
        if not job_id:
            return _json({"ok": False, "error": "job_id is required for action='kill'"})
        res = worker.kill_job(job_id)
        return _json(res)

    return _json({"ok": False, "error": f"Unknown action: {action}. Use 'status', 'list', or 'kill'."})


def _status(args: Dict[str, Any], **_: Any) -> str:
    try:
        return _json(worker.worker_status())
    except Exception as exc:
        logger.exception("harness-worker: status failed")
        return _json({"ok": False, "error": str(exc)})


def _gateway(args: Dict[str, Any], **_: Any) -> str:
    action = str(args.get("action") or "status").strip().lower()
    try:
        if action == "up":
            res = worker.gateway_up()
        elif action == "down":
            res = worker.gateway_down()
        elif action == "status":
            res = worker.gateway_status()
        else:
            res = {"ok": False, "error": "action must be up | down | status."}
    except Exception as exc:
        logger.exception("harness-worker: gateway action failed")
        res = {"ok": False, "error": str(exc)}

    logger.info("harness-worker: gateway action=%s -> %s", action, res.get("ok"))
    return _json(res)


# ---------------------------------------------------------------------------
# 등록
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """훅이 아니라 '도구'를 등록한다 - 이 플러그인의 본체."""
    ctx.register_tool(
        name="dispatch_worker",
        toolset=TOOLSET,
        schema=DISPATCH_SCHEMA,
        handler=_dispatch,
        description="Spawn an external coding harness (Codex / Claude Code) as a worker.",
        emoji="🛠️",
    )
    ctx.register_tool(
        name="worker_job",
        toolset=TOOLSET,
        schema=JOB_SCHEMA,
        handler=_worker_job,
        description="Query, list, or terminate external background coding worker jobs (Codex / Claude Code).",
        emoji="📋",
    )
    ctx.register_tool(
        name="worker_status",
        toolset=TOOLSET,
        schema=STATUS_SCHEMA,
        handler=_status,
        description="Check Codex / Claude Code / LiteLLM gateway / provider readiness.",
        emoji="🔌",
    )
    ctx.register_tool(
        name="worker_gateway",
        toolset=TOOLSET,
        schema=GATEWAY_SCHEMA,
        handler=_gateway,
        description="Start/stop/inspect the LiteLLM gateway that Claude Code needs.",
        emoji="🚪",
    )
    logger.info("harness-worker: registered 4 tools in toolset '%s' "
                "(providers: %s)", TOOLSET, ", ".join(worker.PROVIDERS.keys()))
