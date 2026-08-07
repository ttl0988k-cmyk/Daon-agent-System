/**
 * Event Normalizer — 아키텍처의 핵심 레이어.
 *
 * DAON SSE 원본 이벤트 → Store 상태 변환을 전부 담당한다.
 *   - 토큰 합치기 (token 누적)
 *   - reasoning 합치기
 *   - tool 상태 전이 (start → running → completed/error)
 *   - job progress 갱신
 *   - done / cancel / error 종료 처리
 *
 * UI는 Store의 messages[]만 렌더링하면 된다.
 * 이 레이어는 Roo 타입을 일절 참조하지 않는다.
 */

import { bus } from "../bus/EventBus"
import { useChatStore, nextMessageId, type DaonMessage } from "../stores/chatStore"
import { useToolStore } from "../stores/toolStore"
import { useApprovalStore } from "../stores/approvalStore"
import type {
    TokenEvent,
    ReasoningEvent,
    ToolEvent,
    TerminalOutputEvent,
    JobEvent,
    FileEditEvent,
    FileEditDoneEvent,
    DiffPreviewEvent,
    ApprovalEvent,
    MediaResultEvent,
    ModelInfoEvent,
    ModelFallbackEvent,
    CompressedEvent,
    DoneEvent,
    CancelEvent,
    AppErrorEvent,
    NoticeEvent,
} from "../types"

/** 현재 스트리밍 중인 text 메시지 ID (token 누적을 위한 포인터) */
let activeTextId: string | null = null
/** 현재 스트리밍 중인 reasoning 메시지 ID */
let activeReasoningId: string | null = null

function chat() {
    return useChatStore.getState()
}
function tools() {
    return useToolStore.getState()
}
function approvals() {
    return useApprovalStore.getState()
}

/** 스트림 시작 시 누적 포인터 리셋 */
export function resetStreamState(): void {
    activeTextId = null
    activeReasoningId = null
}

// ── 이벤트 핸들러 ────────────────────────────────────────────────────────────

function onToken(e: TokenEvent): void {
    if (!e.text) return
    // reasoning 직후 token이 오면 reasoning 블록 종료
    if (activeReasoningId) {
        chat().updateMessage(activeReasoningId, { streaming: false })
        activeReasoningId = null
    }
    if (!activeTextId) {
        activeTextId = nextMessageId()
        chat().appendMessage({
            id: activeTextId,
            role: "assistant",
            kind: "text",
            text: "",
            streaming: true,
            ts: Date.now(),
        })
    }
    chat().appendToMessageText(activeTextId, e.text)
}

function onReasoning(e: ReasoningEvent): void {
    if (!e.text) return
    // text 누적 중이면 종료 후 reasoning 블록 시작
    if (activeTextId) {
        chat().updateMessage(activeTextId, { streaming: false })
        activeTextId = null
    }
    if (!activeReasoningId) {
        activeReasoningId = nextMessageId()
        chat().appendMessage({
            id: activeReasoningId,
            role: "assistant",
            kind: "reasoning",
            text: "",
            streaming: true,
            ts: Date.now(),
        })
    }
    chat().appendToMessageText(activeReasoningId, e.text)
}

function onTool(e: ToolEvent): void {
    const store = tools()
    // 백엔드는 'tool.started' / 'tool.completed' / 'tool.output' 를 보낸다.
    // (레거시 'start' / 'complete' 도 허용)
    const isStart = e.event === "start" || e.event === "tool.started"
    const isOutput = e.event === "tool.output"
    if (isOutput) {
        // tool.output — 실행 중 출력 갱신 (running 블록의 output/preview 에 누적)
        const key = store.findRunningByName(e.name)
        if (key) {
            const run = store.runs[key]
            store.updateRun(key, {
                output: `${run?.output || ""}${e.preview || ""}`,
                preview: e.preview || run?.preview,
            })
        }
        return
    }
    if (isStart) {
        const key = `${e.name}-${Date.now()}`
        store.startRun(key, {
            key,
            name: e.name,
            status: "running",
            preview: e.preview,
            args: e.args,
            output: "",
            startTs: Date.now(),
        })
        // 채팅에도 tool 블록 추가
        chat().appendMessage({
            id: key,
            role: "assistant",
            kind: "tool",
            toolName: e.name,
            toolEvent: e.event,
            toolPreview: e.preview,
            toolArgs: e.args,
            toolStatus: "running",
            ts: Date.now(),
        })
        // tool 시작 → 이전 text 누적 종료
        if (activeTextId) {
            chat().updateMessage(activeTextId, { streaming: false })
            activeTextId = null
        }
        if (activeReasoningId) {
            chat().updateMessage(activeReasoningId, { streaming: false })
            activeReasoningId = null
        }
    } else {
        // complete / 기타 종료 이벤트
        const key = store.findRunningByName(e.name)
        if (key) {
            store.updateRun(key, { status: "completed", endTs: Date.now() })
            chat().updateMessage(key, {
                toolStatus: "completed",
                toolPreview: e.preview || undefined,
            })
        } else {
            // running 실행을 못 찾으면 새 완료 블록으로 표시
            const newKey = `${e.name}-${Date.now()}`
            chat().appendMessage({
                id: newKey,
                role: "assistant",
                kind: "tool",
                toolName: e.name,
                toolEvent: e.event,
                toolPreview: e.preview,
                toolArgs: e.args,
                toolStatus: "completed",
                ts: Date.now(),
            })
        }
    }
}

function onTerminalOutput(e: TerminalOutputEvent): void {
    const key = tools().findRunningByName(e.tool)
    if (key) {
        tools().appendOutput(key, e.text)
    }
}

function onJob(e: JobEvent): void {
    const store = tools()
    if (e.type === "start") {
        store.setJob(e.tools || [], true)
    } else if (e.type === "progress") {
        if (e.status === "completed" || e.status === "error") {
            const key = store.findRunningByName(e.tool)
            if (key) {
                store.updateRun(key, {
                    status: e.status === "error" ? "error" : "completed",
                    endTs: Date.now(),
                    duration: e.duration,
                })
                chat().updateMessage(key, {
                    toolStatus: e.status === "error" ? "error" : "completed",
                    toolDuration: e.duration,
                })
            }
        }
        // 모든 도구가 끝나면 job 비활성
        const runs = useToolStore.getState().runs
        const anyRunning = Object.values(runs).some((r) => r.status === "running")
        if (!anyRunning) store.setJob([], false)
    }
}

function onFileEdit(e: FileEditEvent): void {
    if (activeTextId) {
        chat().updateMessage(activeTextId, { streaming: false })
        activeTextId = null
    }
    const id = `fe-${Date.now()}`
    chat().appendMessage({
        id,
        role: "assistant",
        kind: "file_edit",
        toolName: e.name,
        toolArgs: e.args,
        filePath: typeof e.args?.path === "string" ? e.args.path : undefined,
        toolStatus: "running",
        ts: Date.now(),
    })
}

function onFileEditDone(e: FileEditDoneEvent): void {
    // 가장 최근 file_edit 메시지를 찾아 완료 처리
    const msgs = chat().messages
    for (let i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i].kind === "file_edit" && msgs[i].toolStatus === "running") {
            chat().updateMessage(msgs[i].id, {
                toolStatus: "completed",
                filePath: e.path,
                newContent: e.content,
            })
            break
        }
    }
}

function onDiffPreview(e: DiffPreviewEvent): void {
    chat().appendMessage({
        id: `diff-${e.preview_id || Date.now()}`,
        role: "assistant",
        kind: "diff",
        previewId: e.preview_id,
        filePath: e.path,
        oldContent: e.old,
        newContent: e.new_full,
        ts: Date.now(),
    })
}

function onApproval(e: ApprovalEvent): void {
    const key = e.preview_id || `apr-${Date.now()}`
    const kind =
        e.type === "dangerous_command" ? "dangerous_command" : e.preview_id ? "architect_diff" : "generic"
    approvals().addApproval({
        key,
        kind,
        previewId: e.preview_id,
        raw: e,
        ts: Date.now(),
    })
    // 채팅에도 승인 요청 블록 표시
    chat().appendMessage({
        id: `apr-${key}`,
        role: "assistant",
        kind: "approval",
        approval: e,
        previewId: e.preview_id,
        ts: Date.now(),
    })
}

function onMediaResult(e: MediaResultEvent): void {
    chat().appendMessage({
        id: `media-${Date.now()}`,
        role: "assistant",
        kind: "media",
        media: e,
        ts: Date.now(),
    })
}

function onModelInfo(e: ModelInfoEvent): void {
    chat().setLastModel(e.requested)
}

function onModelFallback(e: ModelFallbackEvent): void {
    chat().setLastModel(e.actual || e.requested)
    chat().appendMessage({
        id: `fb-${Date.now()}`,
        role: "system",
        kind: "info",
        text: `모델 폴백: ${e.requested} → ${e.actual || "대체 모델"}`,
        ts: Date.now(),
    })
}

function onCompressed(e: CompressedEvent): void {
    chat().appendMessage({
        id: `cmp-${Date.now()}`,
        role: "system",
        kind: "info",
        text: e.message || "컨텍스트가 자동 압축되었습니다.",
        ts: Date.now(),
    })
}

function onNotice(e: NoticeEvent): void {
    if (!e.message) return
    chat().appendMessage({
        id: `ntc-${Date.now()}`,
        role: "system",
        kind: "info",
        text: e.message,
        ts: Date.now(),
    })
}

function onDone(e: DoneEvent): void {
    // 열려 있는 스트리밍 블록 마무리
    if (activeTextId) {
        chat().updateMessage(activeTextId, { streaming: false })
        activeTextId = null
    }
    if (activeReasoningId) {
        chat().updateMessage(activeReasoningId, { streaming: false })
        activeReasoningId = null
    }
    // running 도구들을 completed로 마무리
    const runs = useToolStore.getState().runs
    for (const run of Object.values(runs)) {
        if (run.status === "running") {
            useToolStore.getState().updateRun(run.key, { status: "completed", endTs: Date.now() })
            chat().updateMessage(run.key, { toolStatus: "completed" })
        }
    }
    chat().setStreamStatus("idle", null)
    if (e.usage) chat().setLastUsage(e.usage)
}

function onCancel(_e: CancelEvent): void {
    if (activeTextId) {
        chat().updateMessage(activeTextId, { streaming: false })
        activeTextId = null
    }
    if (activeReasoningId) {
        chat().updateMessage(activeReasoningId, { streaming: false })
        activeReasoningId = null
    }
    chat().setStreamStatus("idle", null)
}

function onAppError(e: AppErrorEvent): void {
    chat().appendMessage({
        id: `err-${Date.now()}`,
        role: "system",
        kind: "error",
        text: e.message,
        ts: Date.now(),
    })
    // apperror는 스트림을 종료시키지 않을 수 있음 (server.py 기준).
    // 단, rate limit 등 치명적 오류는 종료 처리.
    if (e.type === "rate_limit") {
        chat().setStreamStatus("idle", null)
    }
}

function onError(e: AppErrorEvent): void {
    onAppError(e)
    chat().setStreamStatus("idle", null)
}

// ── 구독 설치 ────────────────────────────────────────────────────────────────

let installed = false

/** Normalizer를 Event Bus에 연결한다. 앱 시작 시 한 번만 호출. */
export function installNormalizer(): void {
    if (installed) return
    installed = true

    bus.on("token", onToken)
    bus.on("reasoning", onReasoning)
    bus.on("tool", onTool)
    bus.on("terminal_output", onTerminalOutput)
    bus.on("job", onJob)
    bus.on("file_edit", onFileEdit)
    bus.on("file_edit_done", onFileEditDone)
    bus.on("diff_preview", onDiffPreview)
    bus.on("approval", onApproval)
    bus.on("media_result", onMediaResult)
    bus.on("model_info", onModelInfo)
    bus.on("model_fallback", onModelFallback)
    bus.on("compressed", onCompressed)
    bus.on("notice", onNotice)
    bus.on("done", onDone)
    bus.on("cancel", onCancel)
    bus.on("error", onError)
    bus.on("apperror", onAppError)
    // heartbeat, speak, agent_log, debate_* 는 메시지 변환 없음
    // (speak은 TTS 사이드 이펙트, agent_log는 하네스 탭용)
}
