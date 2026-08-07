/**
 * View-Model Adapter — 아키텍처에서 **유일하게 Roo 타입을 참조하는 지점**.
 *
 *   DAON Store(DaonMessage[])  →  ClineMessage[]  →  Roo UI
 *
 * 설계 원칙:
 *   - Roo 컴포넌트는 `messages[]`(ClineMessage[])만 렌더링한다.
 *   - DAON의 Store/Normalizer는 Roo 타입을 모른다.
 *   - Roo가 기대하는 렌더링 규칙에 맞춰 DaonMessage를 ClineMessage로 변환한다.
 *
 * Roo 렌더링 규칙 (ChatRow/ChatView 분석 결과):
 *   - 리치 도구 UI는 `ask "tool"` + ClineSayTool JSON 경로만 존재한다.
 *     (`say "tool"`은 runSlashCommand / readCommandOutput만 그리고 나머지는 null)
 *   - reasoning  → say "reasoning" (ReasoningBlock)
 *   - text       → say "text" (Markdown)
 *   - user input → say "user_feedback"
 *   - error      → say "error" (ErrorRow)
 *   - 승인 대기  → ask "tool" / ask "command" (partial=false → Approve/Reject 버튼)
 *   - 스트리밍   → 마지막 메시지 partial===true 이거나 cost 없는 api_req_started
 *
 * 이 파일은 Roo 컴포넌트를 수정하지 않고 동작하도록 하는 접착제다.
 */

import { createTwoFilesPatch } from "diff"

import type { ClineMessage, ClineSayTool } from "@roo-code/types"

import type { DaonMessage, StreamStatus } from "../stores/chatStore"
import type { UsageInfo } from "../types"

// ── 입력 옵션 ────────────────────────────────────────────────────────────────

export interface ViewModelOptions {
    /** 현재 스트림 상태 */
    streamStatus: StreamStatus
    /** 마지막 토큰 사용량 (done 이벤트가 도착했을 때만 설정됨) */
    lastUsage: UsageInfo | null
    /** 대기 중인 승인 존재 여부 (승인 대기 중에는 api_req_started 합성을 건너뜀) */
    hasPendingApproval: boolean
}

// ── 도구 이름 매핑 ───────────────────────────────────────────────────────────

/** 파일 편집 계열 도구 — diff_preview / approval이 리치 UI를 담당하므로 tool 행은 생략 */
const FILE_EDIT_TOOLS = new Set([
    "write_file",
    "write_to_file",
    "patch",
    "apply_diff",
    "apply_patch",
    "edit_file",
    "str_replace_editor",
])

/** 읽기 계열 도구 */
const READ_TOOLS = new Set(["read_file", "read", "view_file", "cat_file"])

/** 명령 실행 계열 도구 */
const COMMAND_TOOLS = new Set([
    "terminal",
    "execute_command",
    "run_command",
    "shell",
    "bash",
    "run_terminal_cmd",
    "computer",
])

/** 검색 계열 도구 */
const SEARCH_TOOLS = new Set(["web_search", "search", "search_files", "grep", "codebase_search", "search_web"])

/** 파일 목록 계열 도구 */
const LIST_TOOLS = new Set(["list_files", "list_directory", "ls", "list_dir"])

/** 이미지 생성 도구 */
const IMAGE_TOOLS = new Set(["generate_image", "image_generation", "create_image"])

/**
 * DAON 도구 이름을 Roo가 렌더링할 수 있는 ClineSayTool.tool 값으로 매핑.
 * 매핑되지 않는 도구는 runSlashCommand로 폴백(항상 렌더링됨)한다.
 */
function buildToolPayload(m: DaonMessage): ClineSayTool {
    const name = m.toolName || "tool"
    const args = m.toolArgs || {}
    const preview = m.toolPreview || ""

    const path = pickString(args, ["path", "file_path", "filePath", "filename", "file"])
    const content = pickString(args, ["content", "text", "patch", "diff", "new_content", "new_str"])
    const command = pickString(args, ["command", "cmd", "script", "code"])
    const query = pickString(args, ["query", "pattern", "regex", "search", "q", "keywords"])

    if (READ_TOOLS.has(name)) {
        return { tool: "readFile", path: path || preview || name }
    }

    if (COMMAND_TOOLS.has(name)) {
        return {
            tool: "runSlashCommand",
            command: command || name,
            args: preview || undefined,
            source: "daon",
        }
    }

    if (SEARCH_TOOLS.has(name)) {
        return { tool: "searchFiles", searchPattern: query || preview || name, regex: query }
    }

    if (LIST_TOOLS.has(name)) {
        return { tool: "listFilesTopLevel", path: path || preview || "." }
    }

    if (IMAGE_TOOLS.has(name)) {
        return { tool: "generateImage", path }
    }

    // 파일 편집 계열은 diff_preview/approval이 담당하지만,
    // diff_preview가 오지 않는 경우를 대비해 편집 블록으로도 그릴 수 있게 반환.
    if (FILE_EDIT_TOOLS.has(name)) {
        return { tool: "editedExistingFile", path: path || preview || name, content: content || preview }
    }

    // 폴백: 항상 렌더링되는 runSlashCommand 블록
    return {
        tool: "runSlashCommand",
        command: name,
        args: preview || summarizeArgs(args),
        source: "daon",
    }
}

function pickString(args: Record<string, unknown>, keys: string[]): string | undefined {
    for (const k of keys) {
        const v = args[k]
        if (typeof v === "string" && v.length > 0) return v
    }
    return undefined
}

function summarizeArgs(args: Record<string, unknown>): string {
    const parts: string[] = []
    for (const [k, v] of Object.entries(args)) {
        if (typeof v === "string") parts.push(`${k}=${v.length > 40 ? v.slice(0, 40) + "…" : v}`)
    }
    return parts.join(" ").slice(0, 120)
}

/** old/new 콘텐츠로부터 unified diff 생성 */
function makeUnifiedDiff(path: string, oldContent: string, newContent: string): string {
    try {
        return createTwoFilesPatch(path || "file", path || "file", oldContent || "", newContent || "", "", "")
    } catch {
        return newContent || ""
    }
}

// ── 개별 메시지 변환 ─────────────────────────────────────────────────────────

/**
 * DaonMessage 하나를 ClineMessage(0개 이상)로 변환.
 * null을 반환하면 채팅에 그리지 않는다(에디터 전용 이벤트 등).
 */
function toClineMessage(m: DaonMessage, all: DaonMessage[]): ClineMessage | null {
    const base = { ts: m.ts }

    switch (m.kind) {
        case "text": {
            if (m.role === "user") {
                return { ...base, type: "say", say: "user_feedback", text: m.text ?? "", partial: m.streaming }
            }
            return { ...base, type: "say", say: "text", text: m.text ?? "", partial: m.streaming }
        }

        case "reasoning": {
            return { ...base, type: "say", say: "reasoning", text: m.text ?? "", partial: m.streaming }
        }

        case "tool": {
            // 파일 편집 도구 tool 행은 diff_preview/approval이 리치 UI를 담당하므로 생략
            if (m.toolName && FILE_EDIT_TOOLS.has(m.toolName)) {
                return null
            }
            const payload = buildToolPayload(m)
            const running = m.toolStatus === "running"
            return {
                ...base,
                type: "ask",
                ask: "tool",
                text: JSON.stringify(payload),
                partial: running,
            }
        }

        case "file_edit": {
            // diff_preview가 리치 diff를 담당 → 채팅 행 생략
            return null
        }

        case "diff": {
            // architect 승인용 diff는 approval 메시지가 담당 → 생략
            const approval = (m as { approval?: unknown }).approval
            const isArchitectApproval =
                m.previewId !== undefined &&
                all.some(
                    (x) =>
                        x.kind === "approval" &&
                        x.previewId === m.previewId &&
                        (x.approval as { type?: string } | undefined)?.type !== "dangerous_command",
                )
            if (isArchitectApproval || approval) {
                return null
            }
            const diffText = makeUnifiedDiff(m.filePath || "", m.oldContent || "", m.newContent || "")
            const payload: ClineSayTool = {
                tool: "appliedDiff",
                path: m.filePath,
                diff: diffText,
                content: diffText,
            }
            return {
                ...base,
                type: "ask",
                ask: "tool",
                text: JSON.stringify(payload),
                partial: false,
            }
        }

        case "approval": {
            const a = (m.approval || {}) as {
                type?: string
                command?: string
                path?: string
                message?: string
                preview_id?: string
                old?: string
                new_full?: string
            }

            // 위험 명령 승인 → ask "command" (Run Command / Reject 버튼)
            if (a.type === "dangerous_command") {
                return {
                    ...base,
                    type: "ask",
                    ask: "command",
                    text: a.command || a.message || "",
                    partial: false,
                }
            }

            // diff 승인 → 같은 preview_id의 diff_preview에서 old/new를 찾아 diff 생성
            const diffSource = all.find((x) => x.kind === "diff" && x.previewId === m.previewId)
            const oldContent = diffSource?.oldContent || (a.old as string | undefined) || ""
            const newContent = diffSource?.newContent || (a.new_full as string | undefined) || ""
            const path = diffSource?.filePath || a.path
            const diffText = makeUnifiedDiff(path || "", oldContent, newContent)

            const payload: ClineSayTool = {
                tool: "appliedDiff",
                path,
                diff: diffText,
                content: diffText,
            }
            return {
                ...base,
                type: "ask",
                ask: "tool",
                text: JSON.stringify(payload),
                partial: false,
            }
        }

        case "media": {
            const media = (m.media || {}) as { url?: string; path?: string; kind?: string; text?: string }
            const label = media.text || media.url || media.path || "미디어 결과"
            return { ...base, type: "say", say: "text", text: String(label), partial: false }
        }

        case "info": {
            return { ...base, type: "say", say: "text", text: m.text ?? "", partial: false }
        }

        case "error": {
            return { ...base, type: "say", say: "error", text: m.text ?? "", partial: false }
        }

        case "terminal": {
            // 터미널 출력은 도구 블록 안에서 표시되므로 별도 행 생략
            return null
        }

        default:
            return null
    }
}

// ── 합성 메시지 ──────────────────────────────────────────────────────────────

/** 스트리밍 중임을 나타내는 api_req_started (cost 없음 → 스피너/취소 버튼) */
function syntheticApiReqStarted(ts: number, model?: string | null): ClineMessage {
    const info = { request: model || undefined }
    return { ts, type: "say", say: "api_req_started", text: JSON.stringify(info), partial: true }
}

/** 태스크 완료 시 보여주는 completion_result (Start New Task 버튼) */
function syntheticCompletion(ts: number, text?: string): ClineMessage {
    return { ts, type: "ask", ask: "completion_result", text: text ?? "", partial: false }
}

// ── 메인 변환 ────────────────────────────────────────────────────────────────

/**
 * DaonMessage 배열을 Roo가 렌더링하는 ClineMessage 배열로 변환.
 *
 * @param daonMessages chatStore의 메시지 배열 (SSE 이벤트로부터 정규화됨)
 * @param options 스트림 상태 / 사용량 / 승인 여부
 */
export function toClineMessages(daonMessages: DaonMessage[], options: ViewModelOptions): ClineMessage[] {
    const { streamStatus, lastUsage, hasPendingApproval } = options
    const result: ClineMessage[] = []

    let lastTs = 0
    const pushUnique = (msg: ClineMessage) => {
        // ts가 단조 증가하도록 보정 (Roo가 ts를 키/식별자로 사용)
        if (msg.ts <= lastTs) {
            msg = { ...msg, ts: lastTs + 1 }
        }
        lastTs = msg.ts
        result.push(msg)
    }

    for (const m of daonMessages) {
        const cline = toClineMessage(m, daonMessages)
        if (cline) pushUnique(cline)
    }

    const isStreamingNow = streamStatus === "streaming" || streamStatus === "connecting"

    // 스트리밍 중인데 마지막 메시지가 partial이 아니면(예: 도구 완료 직후)
    // api_req_started를 합성해 스트리밍 표시를 유지하고 승인 버튼이 잘못 뜨는 것을 막는다.
    // 단, 승인 대기 중에는 버튼을 보여줘야 하므로 합성을 건너뛴다.
    if (isStreamingNow && !hasPendingApproval) {
        const last = result[result.length - 1]
        if (!last || last.partial !== true) {
            pushUnique(syntheticApiReqStarted(Date.now()))
        }
    }

    // 스트림이 끝났고(idle), done으로 usage가 기록됐으며, 승인 대기가 없으면
    // completion_result를 합성해 "Start New Task" 상태를 만든다.
    if (!isStreamingNow && !hasPendingApproval && lastUsage && result.length > 0) {
        const last = result[result.length - 1]
        const alreadyCompleted =
            last && (last.ask === "completion_result" || last.say === "completion_result")
        if (!alreadyCompleted) {
            pushUnique(syntheticCompletion(Date.now()))
        }
    }

    return result
}
