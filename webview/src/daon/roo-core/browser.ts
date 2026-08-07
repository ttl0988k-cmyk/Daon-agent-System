/**
 * `@roo-code/core/browser` 스텁 (DAON 구현).
 *
 * Roo 모노레포에서 `@roo-code/core`는 익스텐션 쪽 로직을 담은 워크스페이스 패키지다.
 * webview는 그중 브라우저에서 안전한 아주 작은 부분집합만 사용한다.
 * DAON에는 해당 패키지가 없으므로, vendored roo-shared 파일들을 "무수정" 상태로
 * 컴파일하기 위해 필요한 함수들을 여기에 재구현한다.
 *
 * 시맨틱은 Roo 원본의 테스트 스펙에서 그대로 재구성했다:
 * - tmp_search/roo-code/src/shared/__tests__/combineApiRequests.spec.ts
 * - tmp_search/roo-code/src/shared/__tests__/combineCommandSequences.spec.ts
 * - tmp_search/roo-code/src/shared/__tests__/getApiMetrics.spec.ts
 */

import type { ClineMessage } from "@roo-code/types"

// ─────────────────────────────────────────────────────────────────────────────
// safeJsonParse
// ─────────────────────────────────────────────────────────────────────────────

/**
 * JSON.parse의 안전한 래퍼. 파싱 실패 시 fallback을 반환한다.
 * (ChatRow, CommandExecution, McpExecution, fileChangesFromMessages에서 사용)
 */
export function safeJsonParse<T>(text: string | undefined | null): T | undefined
export function safeJsonParse<T>(text: string | undefined | null, fallback: T): T
export function safeJsonParse<T>(text: string | undefined | null, fallback?: T): T | undefined {
    if (typeof text !== "string" || text.length === 0) {
        return fallback
    }

    try {
        return JSON.parse(text) as T
    } catch {
        return fallback
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// consolidateApiRequests (roo-shared/combineApiRequests.ts가 re-export)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * `api_req_started` 뒤에 오는 `api_req_finished` 메시지를 시작 메시지로 병합한다.
 * 병합된 메시지는 시작 메시지의 ts/type/say를 유지하고, text는 두 JSON의 병합이다.
 * API 메시지가 하나도 없으면 원본 배열을 그대로(참조 동일) 반환한다.
 */
export function consolidateApiRequests(messages: ClineMessage[]): ClineMessage[] {
    const hasApiMessages = messages.some(
        (m) => m.type === "say" && (m.say === "api_req_started" || m.say === "api_req_finished"),
    )

    if (!hasApiMessages) {
        return messages
    }

    const result: ClineMessage[] = []

    for (const message of messages) {
        if (message.type === "say" && message.say === "api_req_finished") {
            // 가장 가까운 api_req_started를 찾아 병합
            let startedIndex = -1

            for (let i = result.length - 1; i >= 0; i--) {
                if (result[i].type === "say" && result[i].say === "api_req_started") {
                    startedIndex = i
                    break
                }
            }

            if (startedIndex !== -1) {
                const started = result[startedIndex]
                const merged = {
                    ...safeJsonParse<Record<string, unknown>>(started.text, {}),
                    ...safeJsonParse<Record<string, unknown>>(message.text, {}),
                }

                result[startedIndex] = { ...started, text: JSON.stringify(merged) }
                continue
            }
        }

        result.push(message)
    }

    return result
}

// ─────────────────────────────────────────────────────────────────────────────
// consolidateCommands (roo-shared/combineCommandSequences.ts가 re-export)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 명령과 출력이 결합된 텍스트에서 출력 시작을 알리는 구분자.
 * 예: "ls\nOutput:file1.txt\nfile2.txt"
 * (CommandExecution.tsx가 이 문자열로 command/output을 분리한다)
 */
export const COMMAND_OUTPUT_STRING = "\nOutput:"

/**
 * 연속된 command/command_output 메시지를 하나의 command 메시지로 병합하고,
 * use_mcp_server/mcp_server_response 쌍을 response 필드가 포함된 단일 메시지로 병합한다.
 * 병합할 시퀀스가 없으면 원본 배열을 그대로(참조 동일) 반환한다.
 */
export function consolidateCommands(messages: ClineMessage[]): ClineMessage[] {
    const hasSequence = messages.some(
        (m) =>
            (m.type === "ask" && m.ask === "command_output") ||
            (m.type === "say" && m.say === "command_output") ||
            (m.type === "say" && m.say === "mcp_server_response"),
    )

    if (!hasSequence) {
        return messages
    }

    const result: ClineMessage[] = []

    for (const message of messages) {
        // command_output → 직전 command에 이어 붙임
        if (
            (message.type === "ask" && message.ask === "command_output") ||
            (message.type === "say" && message.say === "command_output")
        ) {
            for (let i = result.length - 1; i >= 0; i--) {
                if (result[i].type === "ask" && result[i].ask === "command") {
                    const prev = result[i]
                    const separator = prev.text?.includes(COMMAND_OUTPUT_STRING) ? "\n" : COMMAND_OUTPUT_STRING
                    result[i] = { ...prev, text: `${prev.text ?? ""}${separator}${message.text ?? ""}` }
                    break
                }
            }

            continue
        }

        // mcp_server_response → 직전 use_mcp_server의 JSON에 response 필드로 병합
        if (message.type === "say" && message.say === "mcp_server_response") {
            for (let i = result.length - 1; i >= 0; i--) {
                if (result[i].type === "ask" && result[i].ask === "use_mcp_server") {
                    const prev = result[i]
                    const parsed = safeJsonParse<Record<string, unknown>>(prev.text, {}) ?? {}
                    const existing = typeof parsed.response === "string" ? `${parsed.response}\n` : ""

                    result[i] = {
                        ...prev,
                        text: JSON.stringify({ ...parsed, response: `${existing}${message.text ?? ""}` }),
                    }

                    break
                }
            }

            continue
        }

        result.push(message)
    }

    return result
}

// ─────────────────────────────────────────────────────────────────────────────
// consolidateTokenUsage (roo-shared/getApiMetrics.ts가 getApiMetrics로 re-export)
// ─────────────────────────────────────────────────────────────────────────────

/** api_req_started 메시지의 text(JSON) 구조 */
export type ParsedApiReqStartedTextType = {
    request?: string
    tokensIn?: number
    tokensOut?: number
    cacheWrites?: number
    cacheReads?: number
    cost?: number
    cancelReason?: string
    streamingFailedMessage?: string
    apiProtocol?: string
}

export interface ApiMetrics {
    totalTokensIn: number
    totalTokensOut: number
    totalCacheWrites?: number
    totalCacheReads?: number
    totalCost: number
    contextTokens: number
}

const isNumber = (value: unknown): value is number => typeof value === "number" && !Number.isNaN(value)

/**
 * 메시지 배열 전체의 토큰 사용량/비용을 집계한다.
 * - api_req_started: tokensIn/Out/cacheWrites/cacheReads/cost 누적 (숫자만)
 * - condense_context: cost 누적, contextTokens는 newContextTokens로 갱신
 * - contextTokens: "토큰 정보가 있는 마지막 api_req_started" 또는
 *   "마지막 condense_context" 중 더 뒤의 것으로 결정 (tokensIn + tokensOut)
 */
export function consolidateTokenUsage(messages: ClineMessage[]): ApiMetrics {
    let totalTokensIn = 0
    let totalTokensOut = 0
    let totalCacheWrites: number | undefined
    let totalCacheReads: number | undefined
    let totalCost = 0
    let contextTokens = 0

    for (const message of messages) {
        if (message.type !== "say") {
            continue
        }

        if (message.say === "api_req_started") {
            const parsed = safeJsonParse<ParsedApiReqStartedTextType>(message.text)

            if (!parsed) {
                continue
            }

            if (isNumber(parsed.tokensIn)) totalTokensIn += parsed.tokensIn
            if (isNumber(parsed.tokensOut)) totalTokensOut += parsed.tokensOut
            if (isNumber(parsed.cacheWrites)) totalCacheWrites = (totalCacheWrites ?? 0) + parsed.cacheWrites
            if (isNumber(parsed.cacheReads)) totalCacheReads = (totalCacheReads ?? 0) + parsed.cacheReads
            if (isNumber(parsed.cost)) totalCost += parsed.cost

            // 토큰 정보가 있는 마지막 메시지가 contextTokens를 결정한다.
            // (원본과 동일하게 raw 덧셈 — 숫자가 아닌 값은 그대로 결합될 수 있음)
            if (parsed.tokensIn != null || parsed.tokensOut != null) {
                contextTokens = ((parsed.tokensIn as unknown as number) ?? 0) + ((parsed.tokensOut as unknown as number) ?? 0)
            }
        } else if (message.say === "condense_context") {
            const condense = message.contextCondense

            if (!condense) {
                continue
            }

            if (isNumber(condense.cost)) {
                totalCost += condense.cost
            }

            if (condense.newContextTokens != null) {
                contextTokens = condense.newContextTokens
            }
        }
    }

    return { totalTokensIn, totalTokensOut, totalCacheWrites, totalCacheReads, totalCost, contextTokens }
}

// ─────────────────────────────────────────────────────────────────────────────
// hasTokenUsageChanged / hasToolUsageChanged
// (roo-shared/getApiMetrics.ts가 re-export — webview에서는 직접 사용하지 않음)
// ─────────────────────────────────────────────────────────────────────────────

export function hasTokenUsageChanged(prevMessages: ClineMessage[], nextMessages: ClineMessage[]): boolean {
    const prev = consolidateTokenUsage(prevMessages)
    const next = consolidateTokenUsage(nextMessages)

    return (
        prev.totalTokensIn !== next.totalTokensIn ||
        prev.totalTokensOut !== next.totalTokensOut ||
        prev.totalCacheWrites !== next.totalCacheWrites ||
        prev.totalCacheReads !== next.totalCacheReads ||
        prev.totalCost !== next.totalCost ||
        prev.contextTokens !== next.contextTokens
    )
}

export function hasToolUsageChanged(prevMessages: ClineMessage[], nextMessages: ClineMessage[]): boolean {
    const prevTools = prevMessages.filter((m) => m.type === "ask" && m.ask === "tool")
    const nextTools = nextMessages.filter((m) => m.type === "ask" && m.ask === "tool")

    if (prevTools.length !== nextTools.length) {
        return true
    }

    return prevTools.some((m, i) => m.ts !== nextTools[i].ts || m.text !== nextTools[i].text)
}
