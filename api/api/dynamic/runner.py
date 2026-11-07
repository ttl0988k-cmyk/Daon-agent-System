"""
Node execution with retry/fallback and DAG parallel batch runner.

Provides:
- _build_node_context(): resolves dependency inputs for a node
- _persist_node_result(): records success/failure into results dict and state
- _run_node_with_retries(): executes a single node across fallback model chain
- _handle_timeout_node(): records a timeout failure
- ParallelRunner: orchestrates DAG execution across parallel batches
"""

import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from api.dynamic.state import (
    StreamLogBuffer, NodeMetrics, HermesStateManager, ReviewFailedException
)
from api.dynamic.limits import _load_harness_limits
from api.dynamic.dag_utils import (
    _build_dag_structures, _compute_execution_batches,
    _get_model_chain_for_node, _compress_context, _extract_assistant_content
)
from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)


def _build_node_context(
    node: dict,
    agent_name: str,
    state_manager: "HermesStateManager",
    parent_list: dict,
    main_task: str,
    run_dir: str = None,
) -> tuple[str, list[str]]:
    """Build the agent query string and resolve parent lineage for a DAG node."""
    input_key = node.get("input") or ""
    context_data = ""
    if input_key:
        best_output = state_manager.get_best(input_key)
        if best_output:
            compressed_output = _compress_context(best_output)
            source_agent = next(
                (c.origin for c in state_manager.store.get(input_key, []) if c.value == best_output),
                "unknown",
            )
            context_data = f"\n--- [Input Context from '{source_agent}' ({input_key})] ---\n{compressed_output}\n"
    node_parents = list(parent_list.get(agent_name, []))
    if input_key:
        for c in state_manager.store.get(input_key, []):
            if c.origin not in node_parents:
                node_parents.append(c.origin)

    import sys as _sys, os as _os
    _platform = _sys.platform
    _is_windows = _platform == "win32"
    _os_name = "Windows" if _is_windows else ("macOS" if _platform == "darwin" else "Linux")

    # Detect the shell the terminal tool will ACTUALLY use.
    # On Windows the terminal tool runs commands through Git Bash
    # (tools/environments/local.py _find_bash), NOT cmd.exe. Detecting via
    # COMSPEC misleads the agent into writing backslash paths that bash eats
    # as escapes (C:\dir\file -> C:dirfile), breaking every cd/ls.
    _terminal_is_bash = False
    if _is_windows:
        try:
            from tools.environments.local import _find_bash as _fb
            _terminal_is_bash = bool(_fb())
        except Exception:
            _terminal_is_bash = False
    else:
        _terminal_is_bash = True

    _shell = _os.environ.get('SHELL', '') or _os.environ.get('COMSPEC', '')
    _is_bash = _terminal_is_bash or ('bash' in _shell.lower())
    if _is_windows and _terminal_is_bash:
        _shell_label = "bash (Git Bash on Windows)"
    else:
        _shell_label = _shell if _shell else ('cmd.exe' if _is_windows else '/bin/bash')

    query = f"We are solving this main task: {main_task}\n"
    query += f"\n[SYSTEM ENVIRONMENT]\n"
    query += f"- Operating System: {_os_name} ({_platform})\n"
    query += f"- Shell: {_shell_label}\n"
    if _is_windows and _terminal_is_bash:
        query += ("- Shell WARNING: terminal commands run in Git Bash, NOT cmd.exe. "
                  "Backslashes in Windows paths (C:\\dir\\file) are eaten by bash as escapes. "
                  "ALWAYS use forward slashes (C:/dir/file) in terminal commands.\n")
    elif _is_windows and not _is_bash:
        query += "- Shell WARNING: cmd.exe does NOT support heredoc (<<), sudo, or Unix commands. Use write_file tool instead of cat/echo heredoc. Use PowerShell for advanced scripting.\n"
    elif _is_bash:
        query += "- Bash is available: heredoc (<<), Unix commands (ls, find, grep, cat, etc.) are supported. Use POSIX path style when possible.\n"
    query += "\n"

    # ── 갭 B-4: 실행환경 선택 ──
    # environment == "sandbox" 노드는 공유 워크스페이스(run_dir) 대신
    # 노드 전용 격리 임시 디렉터리에서 파일 작업을 수행한다.
    node_env = str(node.get("environment") or "local").strip().lower()
    effective_dir = run_dir
    sandbox_note = ""
    if node_env == "sandbox":
        import tempfile as _tempfile_mod
        try:
            effective_dir = _tempfile_mod.mkdtemp(prefix=f"daon_sandbox_{agent_name}_")
            sandbox_note = (
                "\n[SANDBOX ENVIRONMENT] This node runs in an ISOLATED sandbox directory, NOT the shared workspace. "
                "Files you create here are experimental and are NOT part of the final deliverable. "
                "If downstream agents need the content of any file you produce, include that content "
                "verbatim in your final output.\n"
            )
            _log.info("Node '%s' runs in sandbox environment: %s", agent_name, effective_dir)
        except Exception as _sb_e:
            _log.warning("Failed to create sandbox dir for node '%s', falling back to local: %s", agent_name, _sb_e)
            effective_dir = run_dir

    if effective_dir:
        # On Windows+Git Bash, present the workspace with forward slashes so it
        # is directly usable in terminal commands (backslashes would be eaten
        # as escapes). Forward slashes also work in all file tools on Windows.
        _dir_label = effective_dir.replace("\\", "/") if (_is_windows and _terminal_is_bash) else effective_dir
        query += f"\n[CRITICAL DIRECTIVE] Your Current Workspace Directory is: '{_dir_label}'. ALL FILE OPERATIONS MUST be done inside this directory. NEVER use relative paths. ALWAYS use absolute paths starting with '{_dir_label}'.\n\n"
    if sandbox_note:
        query += sandbox_note
    # [GATE] 이 노드가 실제로 파일을 쓰는 디렉터리를 node에 기록한다.
    #   node는 호출자가 넘긴 dict이므로 참조로 전달되어, 이후 실행 단계의
    #   결정론적 게이트(_deterministic_gate)가 산출물 경로를 해석할 때 쓰인다.
    try:
        if effective_dir:
            node["_effective_dir"] = str(effective_dir)
    except Exception:
        pass
    query += f"Your specific subtask is: {node['subtask']}\n"
    if context_data:
        query += f"Here is the dependency input data you must utilize:\n{context_data}\n"
    # ══ [GATE] 반환 계약(capa_2 'entrega') ══
    # 노드는 반드시 산출물/판정/증거 3종을 명시해 반환한다.
    # 단, 이 보고는 게이트의 '입력'일 뿐 '판정'이 아니다 — 판정은 파일 실물로만 내려진다.
    query += (
        "\n\n[RETURN CONTRACT — 필수] 최종 응답 끝에 아래 3가지를 반드시 명시하라.\n"
        "1) ARTIFACTS: 생성/수정한 파일의 절대 경로 (한 줄에 하나, 없으면 'NONE')\n"
        "2) VERDICT: PASS | FAIL | PARTIAL\n"
        "3) EVIDENCE: 그 판정의 근거 (측정값·명령 출력·검사 결과 요약)\n"
        "주의: 파일을 만들었다고 썼다면 반드시 실제로 저장되어 있어야 한다. "
        "존재하지 않는 경로를 보고하면 게이트에서 차단된다.\n"
    )
    query += "\nPlease work on this subtask and output your final result clearly."
    return query, node_parents


def _persist_node_result(
    agent_name: str,
    node: dict,
    node_parents: list[str],
    success: bool,
    assistant_content: str,
    last_err: Exception | None,
    model_configs: list[dict],
    node_start_time: float,
    attempts_count: int,
    metrics_entry,
    results: dict,
    state_manager: HermesStateManager,
    generation: int,
    mission_tracker: dict,
    artifacts: list | None = None,
    gate_result: dict | None = None,
) -> None:
    """Persist node execution result into results dict and state_manager."""
    if success:
        results[agent_name] = {
            "name": agent_name,
            "role": node["role"],
            "subtask": node["subtask"],
            "status": "success",
            "output": assistant_content,
            "output_key": node["output"],
            "generation": generation,
            "parents": node_parents,
            "model_used": model_configs[0]["model"] if model_configs else "",
            "duration_seconds": time.time() - node_start_time,
            "attempts": attempts_count,
            # [GATE] 산출물 경로와 게이트 판정을 결과에 남긴다 — 사후 검증/감사 근거.
            "artifacts": artifacts or [],
            "gate": gate_result,
        }
        state_manager.add(
            key=node["output"],
            value=assistant_content,
            origin=agent_name,
            generation=generation,
            parents=node_parents,
            status="success",
        )
        _log.info("Node '%s' completed successfully.", agent_name)
    else:
        fail_output = (
            f"Error: Mission timeout exceeded during execution: {last_err}"
            if isinstance(last_err, TimeoutError)
            else f"Error (Failed after retries & fallbacks): {last_err}"
        )
        results[agent_name] = {
            "name": agent_name,
            "role": node["role"],
            "subtask": node["subtask"],
            "status": "failed",
            "output": fail_output,
            "output_key": node["output"],
            "generation": generation,
            "parents": node_parents,
            "model_used": model_configs[0]["model"] if model_configs else "",
            "duration_seconds": time.time() - node_start_time,
            # [GATE] 실패해도 산출물/게이트 증거는 남긴다 — 재시도 판단 근거.
            "artifacts": artifacts or [],
            "gate": gate_result,
        }
        state_manager.add(
            key=node["output"],
            value=f"Error: {last_err}",
            origin=agent_name,
            generation=generation,
            parents=node_parents,
            status="failed",
        )
        _log.info("Node '%s' FAILED permanently.", agent_name)
        metrics_entry = NodeMetrics(
            node_name=agent_name,
            start_time=node_start_time,
            end_time=time.time(),
            model_used=model_configs[0]["model"],
            provider=model_configs[0]["provider"],
            status="failed",
            attempts=attempts_count,
        )
    if mission_tracker and "add_node_metrics" in mission_tracker and metrics_entry:
        mission_tracker["add_node_metrics"](agent_name, metrics_entry)


# ══════════════════════════════════════════════════════════════════════
# [GATE] capa_3 + capa_5 — 결정론적 코드노드 & 증거 기반 게이트
#   카파시 그래프 프롬프트 원칙: "증거로만 게이트를 연다"
#   ① 결정론적 체크(존재/크기/문법) → ② 노드 보고 → ③ 모델 점수
#   모델이 "SCORE: 95"라고 써도, 파일이 없으면 게이트는 열리지 않는다.
# ══════════════════════════════════════════════════════════════════════
_CHECKS_LOCK = threading.Lock()
_CHECKS_MD_MAX_EVIDENCE = 200


class GateFailedException(RuntimeError):
    """결정론적 게이트 실패 — '이 노드만 고쳐 다시' 대상(capa_6).

    ReviewFailedException(리뷰 점수 미달)과 달리 재시도 가치가 있다:
    파일을 안 썼거나 문법이 깨진 것이므로, 같은 노드를 다시 실행하면 고칠 수 있다.
    따라서 _run_node_with_retries의 즉시중단(break) 조건에 포함하지 않는다.
    """


def _append_checks_md(work_dir: str, node_name: str, checks: list[dict]) -> None:
    """checks.md에 '노드|요구사항|판정|증거' 한 줄씩 append.

    여러 노드가 병렬로 실행되므로 프로세스 내 락으로 직렬화한다.
    기록 실패가 노드 실행을 막아서는 안 되므로 예외는 삼킨다.
    """
    if not work_dir or not checks:
        return
    try:
        path = Path(work_dir) / "checks.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with _CHECKS_LOCK:
            need_header = not path.exists()
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                if need_header:
                    fh.write("# checks.md — 증거 기반 게이트 기록\n")
                    fh.write("# 노드|요구사항|판정|증거\n")
                for c in checks:
                    req = str(c.get("req", "")).replace("|", "/").replace("\n", " ")
                    verdict = str(c.get("verdict", "")).replace("|", "/").replace("\n", " ")
                    ev = str(c.get("evidence", "")).replace("|", "/").replace("\n", " ")
                    fh.write(f"{node_name}|{req}|{verdict}|{ev[:_CHECKS_MD_MAX_EVIDENCE]}\n")
    except Exception as exc:  # noqa: BLE001 - 로깅만 하고 계속
        _log.debug("[gate] checks.md 기록 실패(%s): %s", work_dir, exc)


def _check_syntax(path: Path) -> dict | None:
    """확장자별 결정론적 문법 검사.

    정답이 하나인 검사는 모델에게 묻지 않고 코드로 수행한다(capa_3).
    검사 대상이 아닌 확장자는 None을 반환한다.
    """
    suffix = path.suffix.lower()
    req = f"syntax:{path.name}"
    try:
        if suffix == ".py":
            import ast as _ast
            src = path.read_text(encoding="utf-8", errors="replace")
            if not src.strip():
                return {"req": req, "verdict": "FAIL", "evidence": "빈 파일"}
            try:
                _ast.parse(src, filename=str(path))
            except SyntaxError as se:
                return {"req": req, "verdict": "FAIL", "evidence": f"SyntaxError line {se.lineno}: {se.msg}"}
            return {"req": req, "verdict": "PASS", "evidence": "ast.parse ok"}
        if suffix == ".json":
            import json as _json
            raw = path.read_text(encoding="utf-8", errors="replace")
            try:
                _json.loads(raw)
            except Exception as je:  # noqa: BLE001
                return {"req": req, "verdict": "FAIL", "evidence": f"JSON 오류: {je}"}
            return {"req": req, "verdict": "PASS", "evidence": "json.loads ok"}
        if suffix == ".csv":
            raw = path.read_text(encoding="utf-8", errors="replace")
            lines = [ln for ln in raw.splitlines() if ln.strip()]
            if len(lines) < 2:
                return {"req": req, "verdict": "FAIL", "evidence": "헤더+데이터 행 부족"}
            return {"req": req, "verdict": "PASS", "evidence": f"{len(lines)} rows"}
        if suffix in (".md", ".txt", ".html", ".htm", ".js", ".mjs",
                      ".ts", ".tsx", ".css", ".yaml", ".yml", ".svg"):
            raw = path.read_text(encoding="utf-8", errors="replace")
            if not raw.strip():
                return {"req": req, "verdict": "FAIL", "evidence": "내용이 공백뿐"}
            return None
    except Exception as exc:  # noqa: BLE001
        return {"req": req, "verdict": "SKIP", "evidence": f"검사 불가: {exc}"}
    return None


def _resolve_artifact(raw: str, work_dirs: list[str]) -> Path | None:
    """산출물 경로 후보를 실제 파일로 해석한다(절대 → 워크스페이스 → cwd)."""
    p = str(raw or "").strip().strip('"\'`').rstrip(".,;)")
    if not p:
        return None
    pp = Path(p)
    if pp.is_absolute():
        return pp if pp.exists() else None
    candidates: list[Path] = []
    for wd in (work_dirs or []):
        if wd:
            candidates.append(Path(wd) / p)
    candidates.append(Path.cwd() / p)
    for cand in candidates:
        try:
            if cand.exists():
                return cand
        except OSError:
            continue
    return None


def _deterministic_gate(artifacts: list[str], work_dirs: list[str]) -> dict:
    """capa_3(코드노드) + capa_5(게이트): 파일 시스템 실물로만 판정한다.

    Returns:
        {
          "applicable": bool,   # 산출물을 하나도 안 쓴 노드는 게이트 비대상
          "passed": bool,
          "checks": [{"req","verdict","evidence"}],
          "blockers": [str],
          "resolved": [절대경로...],
        }

    설계 의도:
      - 파일을 '썼다'고 보고하지 않은 노드(분석·기획)는 applicable=False로
        통과시킨다. 게이트가 정상 작업을 막지 않게 하기 위함.
      - 파일을 썼다면 그 파일은 실존하고 0바이트가 아니어야 하며,
        .py/.json 등은 문법이 유효해야 한다. 여기서 FAIL이면
        모델이 몇 점을 주든 게이트는 닫힌 상태로 유지된다.
    """
    artifacts = [a for a in (artifacts or []) if str(a or "").strip()]
    checks: list[dict] = []
    blockers: list[str] = []
    resolved: list[str] = []
    seen: set[str] = set()

    for raw in artifacts:
        key = str(raw).strip()
        if key in seen:
            continue
        seen.add(key)

        target = _resolve_artifact(key, work_dirs)
        if target is None:
            # 절대경로인데 없다 = '존재하지 않는 파일을 썼다'고 보고한 것 → FAIL
            # 상대경로인데 못 찾았다 = 워크스페이스 기준을 우리가 모르는 것 → SKIP
            #   (경로 해석 실패를 FAIL로 처리하면 정상 노드를 막는다)
            if Path(key).is_absolute():
                checks.append({"req": f"exists:{key}", "verdict": "FAIL", "evidence": "파일 없음"})
                blockers.append(f"{key}: 파일이 존재하지 않음")
            else:
                checks.append({"req": f"exists:{key}", "verdict": "SKIP", "evidence": "상대경로 해석 불가"})
            continue

        resolved.append(str(target))
        checks.append({"req": f"exists:{key}", "verdict": "PASS", "evidence": str(target)})

        if target.is_dir():
            try:
                entry_count = len(list(target.iterdir()))
            except OSError:
                entry_count = -1
            if entry_count == 0:
                checks.append({"req": f"nonempty:{key}", "verdict": "FAIL", "evidence": "빈 디렉터리"})
                blockers.append(f"{key}: 빈 디렉터리")
            else:
                checks.append({"req": f"nonempty:{key}", "verdict": "PASS", "evidence": f"{entry_count} entries"})
            continue

        try:
            size = target.stat().st_size
        except OSError as oe:
            checks.append({"req": f"nonempty:{key}", "verdict": "FAIL", "evidence": f"stat 실패: {oe}"})
            blockers.append(f"{key}: 크기 확인 실패")
            continue

        if size <= 0:
            checks.append({"req": f"nonempty:{key}", "verdict": "FAIL", "evidence": "0 bytes"})
            blockers.append(f"{key}: 0바이트")
            continue
        checks.append({"req": f"nonempty:{key}", "verdict": "PASS", "evidence": f"{size} bytes"})

        syntax = _check_syntax(target)
        if syntax:
            checks.append(syntax)
            if syntax.get("verdict") == "FAIL":
                blockers.append(f"{key}: {syntax.get('evidence')}")

    return {
        "applicable": bool(artifacts),
        "passed": not blockers,
        "checks": checks,
        "blockers": blockers,
        "resolved": resolved,
    }


def _extract_review_score(content: str, limits: dict) -> int:
    """Extract a numeric score (0-100) from reviewer/QA agent output.

    Detection priority:
    1. Explicit score format: `SCORE: 85` or `점수: 85` or `Score: 85/100`
    2. Percentage patterns: `85/100`, `85%`
    3. Six-axis counting: count ✅ / total checks, convert to percentage
    4. Binary fallback: ✅ → 100, ❌ → 0, mixed → 50
    """
    # Priority 1: Explicit score marker
    score_patterns = [
        r'(?:SCORE|점수|Score|score)\s*[:：]\s*(\d{1,3})',
        r'(\d{1,3})\s*/\s*100',
        r'(\d{1,3})\s*점',
    ]
    for pattern in score_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            score = int(match.group(1))
            return min(max(score, 0), 100)

    # Priority 2: Percentage pattern
    pct_match = re.search(r'(\d{1,3})\s*%', content)
    if pct_match:
        score = int(pct_match.group(1))
        return min(max(score, 0), 100)

    # Priority 3: Count pass/fail markers for weighted scoring
    pass_count = len(re.findall(r'✅|\[PASS\]|\[통과\]|pass', content, re.IGNORECASE))
    fail_count = len(re.findall(r'❌|⛔|\[FAIL\]|\[실패\]|fail(?:ed)?', content, re.IGNORECASE))
    total_checks = pass_count + fail_count

    if total_checks > 0:
        score = int((pass_count / total_checks) * 100)
        return score

    # Priority 4: Binary fallback
    if re.search(r'✅|\[PASS\]|\[통과\]|all pass|모두 통과', content, re.IGNORECASE):
        return 100
    if re.search(r'❌|⛔|\[FAIL\]|\[실패\]|reject', content, re.IGNORECASE):
        return 0

    return limits.get("scoring", {}).get("pass_threshold", 70)


def _run_node_with_retries(
    agent_name: str,
    node: dict,
    agent_query: str,
    node_parents: list[str],
    model_configs: list[dict],
    limits: dict,
    generation: int,
    state_manager: HermesStateManager,
    mission_tracker: dict,
    results: dict,
    agent_class,
    log_callback=None,
    run_id: str = None,
    run_dir: str = None,
) -> dict:
    """Execute a DAG node across a fallback model chain with per-attempt retry logic."""
    max_retries = limits["node"]["max_retries"]
    success, last_err, assistant_content = False, None, ""
    metrics_entry, attempts_count = None, 0
    node_start_time = time.time()
    # [GATE] 이 노드가 실제로 건드린 파일 경로를 수집한다(결정론적 게이트 입력).
    #   시도(attempt) 간 누적되므로 '한 번이라도 쓴 파일'이 전부 검증 대상이 된다.
    _node_artifacts: list[str] = []
    _gate_result: dict | None = None

    for cfg in model_configs:
        model_name = cfg["model"]
        provider = cfg["provider"]
        api_key = cfg["api_key"]
        base_url = cfg["base_url"]
        for attempt in range(max_retries):
            # 취소된 잡은 재시도하지 않고 즉시 중단한다.
            if run_id:
                try:
                    import api.dynamic_jobs as _dj_cancel
                    if _dj_cancel.is_job_cancelled(run_id):
                        raise RuntimeError("Harness execution cancelled by user")
                except ImportError:
                    pass
            attempts_count += 1
            if mission_tracker and "check_timeout" in mission_tracker:
                try:
                    mission_tracker["check_timeout"]()
                except TimeoutError as te:
                    last_err = te
                    break
            try:
                agent = None  # except 경로에서 안전하게 unregister하기 위한 초기화
                # Extract runtime config from template (if present)
                _rt = node.get("_runtime") or {}
                _agent_kwargs = dict(
                    model=model_name,
                    provider=provider,
                    api_key=api_key,
                    base_url=base_url,
                    enabled_toolsets=node["tools"] or None,
                    quiet_mode=True,
                )
                # Apply runtime overrides from template
                if _rt.get("max_iterations"):
                    _agent_kwargs["max_iterations"] = _rt["max_iterations"]
                if _rt.get("max_tokens"):
                    _agent_kwargs["max_tokens"] = _rt["max_tokens"]
                if _rt.get("temperature") is not None:
                    _agent_kwargs["request_overrides"] = {"temperature": _rt["temperature"]}
                if _rt.get("tool_delay") is not None:
                    _agent_kwargs["tool_delay"] = _rt["tool_delay"]

                agent = agent_class(**_agent_kwargs)
                agent.is_dynamic_runner = True
                # Chat streaming patches the browser tool to use the existing
                # Electron WebContentsView through CDP.  Dynamic Harness runs
                # create agents through this separate runner, so they must
                # apply the same bridge here as well; otherwise a browser tool
                # can launch a standalone window over the DAON UI.
                try:
                    from api.browser_bridge import patch_browser_tool
                    patch_browser_tool()
                except Exception as _browser_bridge_err:
                    _log.warning("Dynamic Harness browser bridge unavailable: %s", _browser_bridge_err)
                # 취소 시 in-flight LLM HTTP 요청을 즉시 중단(interrupt)할 수 있도록 등록.
                if run_id:
                    try:
                        import api.dynamic_jobs as _dj_reg
                        _dj_reg.register_running_agent(run_id, agent)
                    except Exception:
                        pass

                # ── 실시간 에디터 반영: 파일 쓰기 도구 감지 콜백 ──
                # hermes tool_executor는 tool.completed를 (args=None, preview=None)으로 호출한다.
                # 따라서 경로는 args가 제공되는 tool.started 시점에 캡처해 보관했다가,
                # 디스크에 파일이 실제로 쓰인 tool.completed 시점에 [FILE_EDIT] 로그로 발행한다.
                # 프론트엔드(harness.js)가 이 로그를 파싱해 파일 트리/에디터를 갱신한다.
                def _make_file_edit_cb(_agent_name, _sink=None):
                    # 에이전트별로 독립된 pending 맵 (단일 에이전트는 도구를 순차 실행하므로
                    # started→completed 순서가 보장됨). 도구명으로 경로를 보관한다.
                    _pending_paths = {}

                    def _file_edit_cb(event_type, tool_name, preview, args, **kwargs):
                        try:
                            if tool_name not in ('write_file', 'patch'):
                                return
                            if event_type == 'tool.started':
                                # args가 제공되는 유일한 시점 — 경로를 캡처해 보관한다.
                                _sp = ''
                                if isinstance(args, dict):
                                    _sp = args.get('path') or args.get('file_path') or ''
                                if _sp:
                                    _pending_paths[tool_name] = _sp
                                return
                            if event_type != 'tool.completed':
                                return
                            if kwargs.get('is_error'):
                                _pending_paths.pop(tool_name, None)
                                return
                            # started에서 캡처한 경로를 사용한다.
                            _p = _pending_paths.pop(tool_name, '')
                            if not _p and isinstance(args, dict):
                                _p = args.get('path') or args.get('file_path') or ''
                            if not _p and isinstance(preview, str):
                                # preview 예: "write: path/to/file" 또는 경로 자체
                                _p = preview.split(':', 1)[-1].strip() if ':' in preview else preview.strip()
                            if _p:
                                # [GATE] 결정론적 게이트가 나중에 실물을 검증할 경로를 축적한다.
                                #   모델의 자기 보고가 아니라 '실제 tool.completed가 난 경로'만 담긴다.
                                if _sink is not None:
                                    try:
                                        _sink.append(_p)
                                    except Exception:
                                        pass
                                if log_callback:
                                    log_callback(_agent_name, f"[FILE_EDIT]{_p}", "running")
                        except Exception:
                            pass
                    return _file_edit_cb
                agent.tool_progress_callback = _make_file_edit_cb(agent_name, _node_artifacts)

                # === 미디어 생성 도구 registry 등록 (Dynamic Hermes nodes) ===
                # compiler가 모든 노드에 'media-generation' toolset을 부여하므로,
                # 채팅이 먼저 실행되지 않아도 registry에 generate_image/generate_video가
                # 존재하도록 여기서 멱등 등록한다. (save_path 파일 저장 포함)
                try:
                    from tools.registry import registry as _mg_registry
                    from api.media_generation import register_media_generation_tools as _mg_register
                    _mg_register(_mg_registry)
                except Exception as _mg_reg_err:
                    try:
                        if log_callback:
                            log_callback(agent_name, f"[MEDIA] registry 등록 실패: {_mg_reg_err}", "running")
                    except Exception:
                        pass

                # === [NEW] Inject MCP tools into the Hermes Registry (Dynamic Hermes nodes) ===
                # registry는 싱글톤이므로 최초 1회만 등록되지만, Dynamic Hermes가 먼저
                # 실행되는 경우를 대비해 여기에도 동일한 주입 로직을 둔다.
                try:
                    from tools.registry import registry as _mcp_registry
                    from api.mcp_client import get_mcp_manager
                    import re as _mcp_re
                    
                    _mcp_mgr = get_mcp_manager()
                    _mcp_tools = _mcp_mgr.get_all_tools()

                    # ── 갭 B-4: 노드별 MCP 선택 바인딩 ──
                    # node["mcp_servers"] 시맨틱:
                    #   None(생략/null) → 연결된 모든 서버 주입 (기본, 하위 호환)
                    #   []             → MCP 도구 없음
                    #   ["id1", ...]   → 나열된 서버의 도구만 주입
                    _node_mcp_sel = node.get("mcp_servers")
                    if _node_mcp_sel is not None:
                        _allowed_mcp = {str(s).strip() for s in _node_mcp_sel if str(s).strip()}
                        if not _allowed_mcp:
                            _mcp_tools = []
                        else:
                            _mcp_tools = [
                                t for t in _mcp_tools
                                if t.get("_mcp_server") in _allowed_mcp
                            ]
                    
                    def _safe_name(raw: str) -> str:
                        return _mcp_re.sub(r'[^A-Za-z0-9_]', '_', str(raw or ''))
                    
                    def _clean_schema_node(node):
                        if isinstance(node, list):
                            return [_clean_schema_node(item) for item in node]
                        if not isinstance(node, dict):
                            return node
                        import copy
                        n = copy.copy(node)
                        for meta_key in ("$schema", "$id", "title"):
                            n.pop(meta_key, None)
                        if "const" in n:
                            c_val = n.pop("const")
                            if "enum" not in n:
                                n["enum"] = [c_val]
                        if "properties" in n:
                            if isinstance(n["properties"], dict):
                                n["properties"] = {k: _clean_schema_node(v) for k, v in n["properties"].items()}
                            else:
                                n["properties"] = {}
                        if "items" in n and isinstance(n["items"], (dict, list)):
                            n["items"] = _clean_schema_node(n["items"])
                        for comp_key in ("anyOf", "oneOf", "allOf"):
                            if comp_key in n and isinstance(n[comp_key], list):
                                n[comp_key] = [_clean_schema_node(item) for item in n[comp_key]]
                        if not any(k in n for k in ("type", "enum", "anyOf", "oneOf", "allOf", "$ref")):
                            n["type"] = "object" if "properties" in n else "string"
                        return n

                    def _normalize_input_schema(schema):
                        if not schema or not isinstance(schema, dict):
                            return {"type": "object", "properties": {}}
                        cleaned = _clean_schema_node(schema)
                        if cleaned.get("type") != "object":
                            cleaned["type"] = "object"
                        if "properties" not in cleaned or not isinstance(cleaned["properties"], dict):
                            cleaned["properties"] = {}
                        return cleaned
                    
                    _injected_count = 0
                    # ── [Lazy-MCP-Guard 2026-10-22] streaming_tools 의 lazy 분기와 동일 규칙 ──
                    # lazy 모드에서는 개별 mcp_ 스키마를 agent.tools 에 넣지 않는다.
                    # (레지스트리 등록은 그대로 유지 → call_mcp_tool 라우터로 호출 가능)
                    _lazy_mcp = __import__("os").getenv("DAON_LAZY_MCP", "1").strip().lower() not in ("0", "false", "no", "off")
                    if _lazy_mcp:
                        try:
                            _router_names = {"call_mcp_tool", "list_mcp_tools", "mcp_manage"}
                            if hasattr(agent, "tools") and isinstance(agent.tools, list):
                                _before_guard = len(agent.tools)
                                agent.tools = [
                                    _x for _x in agent.tools
                                    if not (
                                        _x.get("function", {}).get("name", "").startswith("mcp_")
                                        and _x.get("function", {}).get("name", "") not in _router_names
                                    )
                                ]
                                print(
                                    "[Lazy-MCP-Guard][runner] lazy=True dropped individual mcp schemas: %d -> %d"
                                    % (_before_guard, len(agent.tools)),
                                    flush=True,
                                )
                        except Exception as _e_rg:
                            print("[Lazy-MCP-Guard][runner] failed: %s" % _e_rg, flush=True)
                        _mcp_tools = []

                    for _t in _mcp_tools:
                        _sid = _t.get('_mcp_server', 'unknown')
                        _oname = _t.get('name', '')
                        _safe_srv = _safe_name(_sid)
                        _safe_tool = _safe_name(_oname)
                        _mcp_fn = f"mcp_{_safe_srv}_{_safe_tool}"
                        _ts_name = f"mcp-{_safe_srv}"
                        
                        # Skip if already in agent.tools (prevent duplicate per-node)
                        _existing_names = {t.get("function", {}).get("name", "") for t in agent.tools}
                        if _mcp_fn in _existing_names:
                            continue
                        
                        # OpenAI-format schema for agent.tools
                        # NOTE: 빈 inputSchema는 모델 API의 "function parameters is empty" 400을
                        # 유발하므로 반드시 정규화(properties 보장)해서 전달한다.
                        _api_schema = {
                            "type": "function",
                            "function": {
                                "name": _mcp_fn,
                                "description": _t.get('description', f"MCP tool {_oname} from {_sid}"),
                                "parameters": _normalize_input_schema(_t.get('inputSchema'))
                            }
                        }
                        agent.tools.append(_api_schema)
                        agent.valid_tool_names.add(_mcp_fn)
                        _injected_count += 1
                        
                        # Register in global registry if not already there (first node only)
                        _existing_reg = [n for n in _mcp_registry.get_all_tool_names() if n == _mcp_fn]
                        if not _existing_reg:
                            # Registry schema
                            _reg_schema = {
                                "name": _mcp_fn,
                                "description": _api_schema["function"]["description"],
                                "parameters": _normalize_input_schema(_t.get('inputSchema')),
                            }
                            
                            def _make_handler(sid, tname):
                                def _handler(args: dict, **kwargs) -> str:
                                    import json as _json
                                    result = _mcp_mgr.call_tool(sid, tname, args)
                                    if result.get('ok'):
                                        payload = result.get('result', 'Success')
                                        if isinstance(payload, str):
                                            return _json.dumps({"result": payload}, ensure_ascii=False)
                                        return _json.dumps({"result": _json.dumps(payload, ensure_ascii=False, default=str)}, ensure_ascii=False)
                                    else:
                                        return _json.dumps({"error": result.get('error', 'Unknown error')}, ensure_ascii=False)
                                return _handler
                            
                            def _make_check_fn(sid):
                                def _check() -> bool:
                                    conn = _mcp_mgr._connections.get(sid)
                                    return conn is not None and conn.connected
                                return _check
                            
                            _mcp_registry.register(
                                name=_mcp_fn,
                                toolset=_ts_name,
                                schema=_reg_schema,
                                handler=_make_handler(_sid, _oname),
                                check_fn=_make_check_fn(_sid),
                                is_async=False,
                                description=_reg_schema["description"],
                            )
                            
                            _alias = _ts_name.replace("mcp-", "", 1)
                            _mcp_registry.register_toolset_alias(_alias, _ts_name)
                    
                    if _injected_count > 0:
                        _log.info("[DynamicHermes] Injected %d MCP tools for node '%s'.", _injected_count, agent_name)
                except Exception as _mcp_e:
                    _log.debug("[DynamicHermes] MCP injection skipped for node '%s': %s", agent_name, _mcp_e)
                # ========================================================

                # === 시나리오 D: DiscoveryBoard 도구 노출 (broadcast/check) ===
                # 오케스트레이터가 보드를 생성해 mission_tracker에 실은 경우에만
                # broadcast_discovery / check_team_discoveries 도구를 노출한다.
                # 보드가 없거나 비활성이면 통째로 건너뛰어 기본 경로를 보존한다.
                # (registry 등록은 멱등, agent.tools 주입은 MCP 패턴 재사용)
                # 실증 라운드 2 교훈: 도구 노출만으로는 자발적 방송 0건.
                # 도구 노출 시 DISCOVERY_NUDGE_PROMPT를 시스템 프롬프트에 주입해
                # "병렬 형제는 내 출력을 못 본다"는 유인을 부여한다.
                _disc_nudge_active = False
                try:
                    _d_board = (mission_tracker or {}).get("discovery_board") if isinstance(mission_tracker, dict) else None
                    if _d_board is not None and getattr(_d_board, "enabled", False):
                        from tools.registry import registry as _disc_registry
                        from api.dynamic.discovery_board import (
                            register_discovery_tools as _disc_register,
                            BROADCAST_SCHEMA as _DISC_BROADCAST_SCHEMA,
                            CHECK_SCHEMA as _DISC_CHECK_SCHEMA,
                            TOOL_BROADCAST_DISCOVERY as _DISC_BROADCAST_NAME,
                            TOOL_CHECK_TEAM_DISCOVERIES as _DISC_CHECK_NAME,
                        )
                        _disc_register(_disc_registry)
                        _disc_existing = {t.get("function", {}).get("name", "") for t in agent.tools}
                        for _disc_name, _disc_schema in (
                            (_DISC_BROADCAST_NAME, _DISC_BROADCAST_SCHEMA),
                            (_DISC_CHECK_NAME, _DISC_CHECK_SCHEMA),
                        ):
                            if _disc_name in _disc_existing:
                                continue
                            agent.tools.append({
                                "type": "function",
                                "function": {
                                    "name": _disc_name,
                                    "description": _disc_schema["description"],
                                    "parameters": _disc_schema["parameters"],
                                },
                            })
                            agent.valid_tool_names.add(_disc_name)
                        _disc_nudge_active = True
                        _log.info("[DiscoveryBoard] Tools exposed for node '%s'.", agent_name)
                except Exception as _disc_e:
                    _log.debug("[DiscoveryBoard] tool exposure skipped for '%s': %s", agent_name, _disc_e)

                buffer = StreamLogBuffer(f"{agent_name} ({model_name})", log_callback)
                def stream_cb(chunk):
                    buffer.write(chunk)

                # ── 에이전트 간 메시징: 실행 전 수신함(inbox)을 시스템 프롬프트에 주입 ──
                _sys_prompt = node["system_prompt"]
                try:
                    from api.memory_store import format_inbox_prompt
                    _inbox_prompt = format_inbox_prompt(agent_name)
                    if _inbox_prompt:
                        _sys_prompt = (_sys_prompt or "") + "\n\n" + _inbox_prompt
                        _log.info("[DynamicHermes] Injected inbox for node '%s'.", agent_name)
                except Exception as _inbox_e:
                    _log.debug("[DynamicHermes] inbox injection skipped for '%s': %s", agent_name, _inbox_e)

                # ── 시나리오 D: DiscoveryBoard 프롬프트 유도(nudge) 주입 ──
                # 도구가 노출된 노드에만 주입. 절대 raise하지 않는다.
                if _disc_nudge_active:
                    try:
                        from api.dynamic.discovery_board import DISCOVERY_NUDGE_PROMPT as _disc_nudge
                        _sys_prompt = (_sys_prompt or "") + "\n\n" + _disc_nudge
                        _log.info("[DiscoveryBoard] Nudge prompt injected for node '%s'.", agent_name)
                    except Exception as _nudge_e:
                        _log.debug("[DiscoveryBoard] nudge injection skipped for '%s': %s", agent_name, _nudge_e)

                # ══ [GATE] capa_6 — '이 노드만 고쳐 다시' ══
                # 재시도 시 직전 실패 사유를 프롬프트에 되먹인다.
                # 이게 없으면 같은 실수를 그대로 반복하고 재시도가 무의미해진다.
                _retry_query = agent_query
                if attempts_count > 1 and last_err is not None:
                    _prev_reason = str(last_err)[:1200]
                    _retry_query = (
                        agent_query
                        + "\n\n[RETRY FEEDBACK — 직전 시도 실패]\n"
                        + _prev_reason
                        + "\n\n위 실패 사유를 반드시 해결한 뒤 다시 제출하라. "
                          "특히 ARTIFACTS에 적은 경로에 파일이 실제로 저장되어 있어야 한다.\n"
                    )
                    _log.info("[GATE] node '%s' retry with feedback (attempt %d).", agent_name, attempts_count)

                res = agent.run_conversation(
                    user_message=_retry_query,
                    system_message=_sys_prompt,
                    stream_callback=stream_cb
                )
                buffer.flush()
                if res.get("failed"):
                    raise RuntimeError(res.get("error"))
                if res.get("id") == "partial-stream-stub":
                    raise RuntimeError("Stream dropped mid-generation (partial stub returned). Triggering fallback.")
                assistant_content = _extract_assistant_content(res.get("messages", []))

                # ── 에이전트 간 메시징: 출력의 [MSG to=X cc=Y]...[/MSG] 블록을 파싱·발송 ──
                try:
                    from api.memory_store import parse_and_dispatch_messages
                    _run_id = (mission_tracker or {}).get("run_id") if isinstance(mission_tracker, dict) else None
                    assistant_content, _sent_msgs = parse_and_dispatch_messages(
                        agent_name, assistant_content, run_id=_run_id
                    )
                    if _sent_msgs:
                        _log.info("[DynamicHermes] Node '%s' dispatched %d agent message(s).", agent_name, _sent_msgs)
                except Exception as _msg_e:
                    _log.debug("[DynamicHermes] message dispatch skipped for '%s': %s", agent_name, _msg_e)

                if log_callback:
                    log_callback(f"{agent_name} ({model_name})", "\n", "done")
                if not assistant_content.strip():
                    raise RuntimeError("Agent returned empty assistant content.")

                # ══ [GATE] capa_5 — "증거로만 게이트를 연다" ══
                # 판정 순서 고정: ① 결정론(파일 실물) → ② 노드 보고 → ③ 모델 점수
                # ①이 FAIL이면 ③의 점수와 무관하게 게이트는 닫힌 채로 남는다.
                # (기존 코드는 ③만 있어서, 모델이 자기 글에 "SCORE: 95"라고 쓰면 통과했다)
                _gate_dirs = [d for d in (node.get("_effective_dir"), run_dir) if d]
                _gate_result = _deterministic_gate(_node_artifacts, _gate_dirs)
                if _gate_result.get("applicable"):
                    _log.info(
                        "[GATE] node '%s' deterministic=%s checks=%d blockers=%d",
                        agent_name,
                        "PASS" if _gate_result.get("passed") else "FAIL",
                        len(_gate_result.get("checks") or []),
                        len(_gate_result.get("blockers") or []),
                    )
                    for _bl in (_gate_result.get("blockers") or []):
                        _log.info("[GATE] node '%s' blocker: %s", agent_name, _bl)
                    if log_callback:
                        try:
                            _summary = "PASS" if _gate_result.get("passed") else "FAIL"
                            log_callback(agent_name, f"[GATE] 결정론 검증: {_summary} "
                                         f"(checks={len(_gate_result.get('checks') or [])}, "
                                         f"blockers={len(_gate_result.get('blockers') or [])})", "running")
                        except Exception:
                            pass
                    _append_checks_md(
                        (node.get("_effective_dir") or run_dir or ""),
                        agent_name,
                        _gate_result.get("checks") or [],
                    )
                    if not _gate_result.get("passed"):
                        raise GateFailedException(
                            "[GATE] 결정론적 검증 실패 — 모델 자기보고와 무관하게 차단.\n"
                            + "\n".join(f"- {_b}" for _b in (_gate_result.get("blockers") or []))
                        )

                # [NEW] Semantic QA Feedback Loop with Scoring System
                role_str = str(node.get("role", "")).lower()
                if "qa" in role_str or "review" in role_str or "검수" in role_str:
                    score = _extract_review_score(assistant_content, limits)
                    pass_threshold = limits.get("scoring", {}).get("pass_threshold", 70)
                    if score < pass_threshold:
                        raise ReviewFailedException(
                            f"Reviewer score {score}/100 below threshold {pass_threshold}. "
                            f"Feedback:\n{assistant_content[:1500]}"
                        )
                    _log.info("Node '%s' QA score: %d/100 (threshold: %d) ✅", agent_name, score, pass_threshold)

                success = True
                metrics_entry = NodeMetrics(
                    node_name=agent_name,
                    start_time=node_start_time,
                    end_time=time.time(),
                    model_used=model_name,
                    provider=provider,
                    status="success",
                    attempts=attempts_count,
                    input_tokens=res.get("input_tokens", 0),
                    output_tokens=res.get("output_tokens", 0),
                    cache_read_tokens=res.get("cache_read_tokens", 0),
                    cache_write_tokens=res.get("cache_write_tokens", 0),
                    reasoning_tokens=res.get("reasoning_tokens", 0),
                )
                if run_id and agent is not None:
                    try:
                        import api.dynamic_jobs as _dj_unreg
                        _dj_unreg.unregister_running_agent(run_id, agent)
                    except Exception:
                        pass
                break
            except Exception as e:
                if run_id and agent is not None:
                    try:
                        import api.dynamic_jobs as _dj_unreg2
                        _dj_unreg2.unregister_running_agent(run_id, agent)
                    except Exception:
                        pass
                last_err = e
                _log.info(
                    "Node '%s' failed with '%s' (Attempt %d/%d): %s",
                    agent_name, model_name, attempt + 1, max_retries, e,
                )
                if attempt < max_retries - 1:
                    sleep_sec = (2 ** attempt) * 3 + random.uniform(0.5, 2.0)
                    _log.info("Retrying in %.2f seconds...", sleep_sec)
                    time.sleep(sleep_sec)
        if success or isinstance(last_err, TimeoutError) or isinstance(last_err, ReviewFailedException):
            break

    _persist_node_result(
        agent_name,
        node,
        node_parents,
        success,
        assistant_content,
        last_err,
        model_configs,
        node_start_time,
        attempts_count,
        metrics_entry,
        results,
        state_manager,
        generation,
        mission_tracker,
        _node_artifacts,
        _gate_result,
    )
    return results[agent_name]


def _handle_timeout_node(
    node_name: str,
    agent_by_name: dict,
    node_timeout: float,
    generation: int,
    parent_list: dict,
    state_manager: HermesStateManager,
    results: dict,
    mission_tracker: dict,
) -> None:
    """Record a timeout failure for a node that exceeded its wall-time limit."""
    _log.info("Node '%s' wall-time limit exceeded (%ss).", node_name, node_timeout)
    node = agent_by_name.get(node_name)
    node_parents = list(parent_list.get(node_name, []))
    results[node_name] = {
        "name": node_name,
        "role": node["role"],
        "subtask": node["subtask"],
        "status": "failed",
        "output": f"Error: Node execution wall-time limit exceeded ({node_timeout}s)",
        "output_key": node["output"],
        "generation": generation,
        "parents": node_parents,
    }
    state_manager.add(
        key=node["output"],
        value="Error: Timeout",
        origin=node_name,
        generation=generation,
        parents=node_parents,
        status="failed",
    )
    metrics_entry = NodeMetrics(
        node_name=node_name,
        start_time=time.time() - node_timeout,
        end_time=time.time(),
        model_used=node.get("model") or "unknown",
        provider="unknown",
        status="timeout",
        attempts=1,
    )
    if mission_tracker and "add_node_metrics" in mission_tracker:
        mission_tracker["add_node_metrics"](node_name, metrics_entry)


class ParallelRunner:
    """Execute DAG nodes in parallel batches via topological sort."""

    @staticmethod
    def run(
        agents: list[dict],
        edges: list[list[str]],
        main_task: str,
        initial_outputs: list[dict] = None,
        state_manager: "HermesStateManager" = None,
        generation: int = 0,
        mission_tracker: dict = None,
        log_callback=None,
        run_dir: str = None,
        session_id: str = None,
        run_id: str = None,
    ) -> list[dict]:
        """Execute DAG nodes in parallel batches via topological sort. Resolves data flows and failures."""
        agent_path = str(Path(__file__).resolve().parent.parent.parent.parent / "hermes-agent")
        if agent_path not in sys.path:
            sys.path.append(agent_path)
        from run_agent import AIAgent

        limits = _load_harness_limits()
        node_timeout = limits["node"]["max_wall_time_seconds"]
        results: dict = {}
        agent_by_name = {a["name"]: a for a in agents}

        if state_manager is None:
            state_manager = HermesStateManager()
        if initial_outputs:
            for out in initial_outputs:
                state_manager.add(
                    key=out["output_key"],
                    value=out["content"],
                    origin=out["name"],
                    generation=out.get("generation", 0),
                    parents=out.get("parents", []),
                    status="success",
                )

        in_degree, adj_list, parent_list = _build_dag_structures(agents, edges)
        batches = _compute_execution_batches(in_degree, adj_list)
        _log.info("Computed Execution Batches: %s", batches)
        from api.dynamic.model_selector import get_allowed_providers, set_allowed_providers
        current_allowed = get_allowed_providers()

        # 갭 D: 노드 워커 스레드에 전달할 위임 컨텍스트 (delegate_team 가드 판정 근거)
        _delegation_ctx = (mission_tracker or {}).get("delegation")
        try:
            from api.dynamic.delegation import set_current_delegation, clear_current_delegation
        except Exception:
            set_current_delegation = None
            clear_current_delegation = None

        # ── 시나리오 D: DiscoveryBoard (수동적 인지 계층) ──
        # 오케스트레이터가 mission_tracker["discovery_board"]에 실어 보낸 보드를
        # 읽어 노드 워커 스레드에 thread-local로 노출한다. None이면(비활성/미생성)
        # 모든 배선이 안전하게 건너뛰어진다 (회귀 안전 기본 경로).
        _discovery_board = None
        try:
            _discovery_board = (mission_tracker or {}).get("discovery_board")
        except Exception:
            _discovery_board = None

        def _run_single_node(agent_name: str) -> dict:
            """Thin closure: resolve context then delegate to module-level runner."""
            set_allowed_providers(current_allowed)
            if session_id:
                try:
                    from tools.approval import set_current_session_key
                    set_current_session_key(session_id)
                except ImportError:
                    pass
            if mission_tracker and "check_timeout" in mission_tracker:
                mission_tracker["check_timeout"]()
            node = agent_by_name.get(agent_name)
            if not node:
                return {"status": "failed", "error": "Node definition not found"}
            _log.info("Executing Node: %s (Type: %s, Role: %s)", agent_name, node['type'], node['role'])
            agent_query, node_parents = _build_node_context(node, agent_name, state_manager, parent_list, main_task, run_dir)
            # 시나리오 D: 노드 시작 시점에 팀 발견 digest를 컨텍스트에 주입한다.
            # 에이전트가 check 도구를 호출하기 전에도 "팀이 이미 발견한 것"을 인지한다.
            if _discovery_board is not None:
                try:
                    _digest = _discovery_board.compress(
                        agent_name=agent_name, subtask=node.get("subtask") or "")
                    if _digest:
                        agent_query = agent_query + "\n\n" + _digest
                        _log.info("[DiscoveryBoard] Digest injected for node '%s'.", agent_name)
                except Exception as _digest_e:
                    _log.debug("[DiscoveryBoard] digest injection skipped for '%s': %s", agent_name, _digest_e)
            _node_role = node.get("role", "")
            _node_type = node.get("type", "llm")
            _node_strength = "code"
            _node_ctx = 32000
            try:
                from api.dynamic.model_selector import DynamicModelSelector
                _node_strength = DynamicModelSelector.infer_strength_from_role(_node_role, _node_type)
                _node_ctx = DynamicModelSelector.estimate_context_tokens(node.get("subtask", main_task), _node_role)
            except Exception as e:
                _log.warning("Failed to infer model selection metadata: %s", e)
            model_configs = _get_model_chain_for_node(
                node.get("model") or "",
                role=_node_role,
                task=node.get("subtask") or main_task,
                required_strength=_node_strength,
                required_context=_node_ctx,
            )
            # ── 노드 카드 즉시 생성: 에이전트 실행 시작을 알려 프론트엔드 카드가 바로 뜨게 한다 ──
            # 스트리밍 로그(StreamLogBuffer)는 "agent_name (model)" 키로 발행하므로,
            # 시작 로그도 동일 키로 맞춰야 카드가 하나로 유지된다.
            if log_callback and model_configs:
                try:
                    _start_key = f"{agent_name} ({model_configs[0].get('model', '')})"
                    log_callback(_start_key, "작업 시작...", "running")
                except Exception:
                    pass
            # 갭 D: 위임 컨텍스트를 thread-local으로 노출한다. 노드 에이전트의
            # 도구 호출(delegate_team)은 모두 이 워커 스레드에서 실행된다.
            if _delegation_ctx and set_current_delegation:
                _node_delegation = dict(_delegation_ctx)
                _node_delegation["node_name"] = agent_name
                set_current_delegation(_node_delegation)
            # 시나리오 D: 보드 + 호출자 이름을 thread-local으로 노출한다.
            # broadcast/check 도구 핸들러가 이 워커 스레드에서 실행되며 읽는다.
            if _discovery_board is not None:
                try:
                    from api.dynamic.discovery_board import set_current_board
                    set_current_board(_discovery_board, agent_name)
                except Exception:
                    pass
            try:
                return _run_node_with_retries(
                    agent_name,
                    node,
                    agent_query,
                    node_parents,
                    model_configs,
                    limits,
                    generation,
                    state_manager,
                    mission_tracker,
                    results,
                    AIAgent,
                    log_callback,
                    run_id,
                    run_dir,
                )
            finally:
                if clear_current_delegation:
                    clear_current_delegation()
                if _discovery_board is not None:
                    try:
                        from api.dynamic.discovery_board import clear_current_board
                        clear_current_board()
                    except Exception:
                        pass

        max_workers = max(len(b) for b in batches) if batches else 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for batch in batches:
                if run_id:
                    import api.dynamic_jobs as dj
                    if dj.is_job_cancelled(run_id):
                        _log.warning("Harness execution cancelled by user!")
                        raise Exception("Harness execution cancelled by user")

                if mission_tracker and "check_timeout" in mission_tracker:
                    mission_tracker["check_timeout"]()
                _log.info("Running Batch in Parallel: %s", batch)
                futures = {executor.submit(_run_single_node, name): name for name in batch}
                for future in futures:
                    node_name = futures[future]
                    try:
                        # future.result(timeout=node_timeout)는 한 번 블록되면 취소가
                        # 반영되지 않는다. 1초 단위로 폴링하며 is_job_cancelled를 체크해
                        # 취소 즉시 실행 루프를 빠져나갈 수 있게 한다.
                        _deadline = time.time() + node_timeout
                        while True:
                            try:
                                future.result(timeout=1.0)
                                break  # 노드 정상 완료
                            except TimeoutError:
                                if run_id:
                                    try:
                                        import api.dynamic_jobs as _dj_poll
                                        if _dj_poll.is_job_cancelled(run_id):
                                            raise Exception("Harness execution cancelled by user")
                                    except ImportError:
                                        pass
                                if time.time() >= _deadline:
                                    raise  # 실제 wall-time 초과 → 아래 except TimeoutError로
                    except TimeoutError:
                        _handle_timeout_node(
                            node_name,
                            agent_by_name,
                            node_timeout,
                            generation,
                            parent_list,
                            state_manager,
                            results,
                            mission_tracker,
                        )
                    except Exception as e:
                        _log.info("Node '%s' raised execution exception: %s", node_name, e)

        return [results[name] for batch in batches for name in batch if name in results]
