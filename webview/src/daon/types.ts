/**
 * DAON 백엔드 SSE 프로토콜 타입 정의.
 * api/api/streaming.py의 put() 이벤트와 1:1 대응.
 * 이 파일은 Roo 타입을 일절 참조하지 않는다 (독립 계층).
 */

// ── 원본 SSE 이벤트 페이로드 ─────────────────────────────────────────────────

export interface TokenEvent {
    text: string
}

export interface ReasoningEvent {
    text: string
}

export interface ToolEvent {
    name: string
    event: "start" | "complete" | string
    preview: string
    args: Record<string, unknown>
}

export interface TerminalOutputEvent {
    tool: string
    text: string
}

export interface JobEvent {
    type: "start" | "progress"
    tool: string
    status?: "completed" | "error"
    duration?: number
    preview?: string
    tools: string[]
}

export interface FileEditEvent {
    name: string
    args: Record<string, unknown>
}

export interface FileEditDoneEvent {
    name: string
    path: string
    content: string
}

export interface DiffPreviewEvent {
    preview_id: string
    path: string
    old?: string
    new_full?: string
    architect_approval?: boolean
    [key: string]: unknown
}

export interface ApprovalEvent {
    preview_id?: string
    type?: string
    [key: string]: unknown
}

export interface MediaResultEvent {
    [key: string]: unknown
}

export interface ModelInfoEvent {
    requested: string
    [key: string]: unknown
}

export interface ModelFallbackEvent {
    requested: string
    actual?: string
    [key: string]: unknown
}

export interface CompressedEvent {
    message: string
    [key: string]: unknown
}

export interface SpeakEvent {
    text: string
    tool?: string
    event?: string
    summary?: boolean
}

export interface DoneEvent {
    session?: SessionResponse
    usage?: UsageInfo
    job_error?: boolean
    text?: string
}

export interface CancelEvent {
    message?: string
}

export interface AppErrorEvent {
    message: string
    type?: string
}

export interface NoticeEvent {
    message?: string
    [key: string]: unknown
}

export interface HeartbeatEvent {
    [key: string]: unknown
}

// ── 세션/설정 응답 ───────────────────────────────────────────────────────────

export interface SessionResponse {
    session_id: string
    title?: string
    workspace?: string
    model?: string
    messages?: SessionMessage[]
    tool_calls?: unknown[]
    [key: string]: unknown
}

export interface SessionMessage {
    role: "user" | "assistant" | "system" | string
    content: string
    [key: string]: unknown
}

export interface SessionListItem {
    session_id: string
    title: string
    created_at?: string
    updated_at?: string
    message_count?: number
    pinned?: boolean
    archived?: boolean
    [key: string]: unknown
}

export interface UsageInfo {
    input_tokens?: number
    output_tokens?: number
    cost?: number
    [key: string]: unknown
}

// ── 이벤트 이름 유니온 ───────────────────────────────────────────────────────

export type DaonEventName =
    | "token"
    | "reasoning"
    | "tool"
    | "terminal_output"
    | "job"
    | "file_edit"
    | "file_edit_done"
    | "diff_preview"
    | "approval"
    | "media_result"
    | "heartbeat"
    | "model_info"
    | "model_fallback"
    | "compressed"
    | "speak"
    | "done"
    | "cancel"
    | "error"
    | "apperror"
    | "notice"
    | "debate_token"
    | "debate_status"
    | "debate_message_done"
    | "agent_log"
    | "patch_warning"

/** 스트림을 종료시키는 이벤트 (server.py handle_sse 기준) */
export const TERMINAL_EVENTS: ReadonlySet<string> = new Set(["done", "error", "cancel"])

/** SSE 이벤트 페이로드 매핑 (Event Bus용) */
export interface DaonEventMap {
    token: TokenEvent
    reasoning: ReasoningEvent
    tool: ToolEvent
    terminal_output: TerminalOutputEvent
    job: JobEvent
    file_edit: FileEditEvent
    file_edit_done: FileEditDoneEvent
    diff_preview: DiffPreviewEvent
    approval: ApprovalEvent
    media_result: MediaResultEvent
    heartbeat: HeartbeatEvent
    model_info: ModelInfoEvent
    model_fallback: ModelFallbackEvent
    compressed: CompressedEvent
    speak: SpeakEvent
    done: DoneEvent
    cancel: CancelEvent
    error: AppErrorEvent
    apperror: AppErrorEvent
    notice: NoticeEvent
    debate_token: Record<string, unknown>
    debate_status: Record<string, unknown>
    debate_message_done: Record<string, unknown>
    agent_log: Record<string, unknown>
    patch_warning: Record<string, unknown>
}
