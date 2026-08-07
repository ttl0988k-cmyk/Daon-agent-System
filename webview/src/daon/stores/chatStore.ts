/**
 * Chat Store — 채팅 메시지 상태.
 *
 * 설계 원칙 #1: 이 Store는 Roo의 ClineMessage 구조를 모방하지 않는다.
 * DAON 고유의 중간 표현(DaonMessage)을 사용하고,
 * Roo가 기대하는 형태는 adapter/viewModel.ts에서만 변환한다.
 */

import { create } from "zustand"
import type { UsageInfo } from "../types"

export type DaonMessageRole = "user" | "assistant" | "system"
export type DaonMessageKind =
    | "text" // LLM 응답 텍스트 (token 누적)
    | "reasoning" // thinking (reasoning 누적)
    | "tool" // 도구 실행 블록
    | "terminal" // 터미널 출력
    | "file_edit" // 파일 편집
    | "diff" // diff 미리보기
    | "approval" // 승인 요청
    | "media" // 이미지/영상 결과
    | "info" // 안내 (compressed, notice 등)
    | "error" // 오류

export interface DaonMessage {
    /** 고유 ID (타임스탬프 기반) */
    id: string
    role: DaonMessageRole
    kind: DaonMessageKind
    /** 텍스트 콘텐츠 (text/reasoning/info/error) */
    text?: string
    /** 스트리밍 중 여부 (텍스트 누적 중) */
    streaming?: boolean
    /** 도구 관련 (kind=tool/terminal/file_edit) */
    toolName?: string
    toolEvent?: string
    toolPreview?: string
    toolArgs?: Record<string, unknown>
    toolStatus?: "running" | "completed" | "error"
    toolDuration?: number
    /** diff 관련 */
    previewId?: string
    filePath?: string
    oldContent?: string
    newContent?: string
    /** 미디어 관련 */
    media?: Record<string, unknown>
    /** 승인 관련 */
    approval?: Record<string, unknown>
    /** 생성 시각 (ms) */
    ts: number
}

export type StreamStatus = "idle" | "connecting" | "streaming"

interface ChatState {
    /** 현재 세션의 메시지 목록 */
    messages: DaonMessage[]
    /** 스트림 상태 */
    streamStatus: StreamStatus
    /** 활성 stream_id */
    streamId: string | null
    /** 마지막 사용량 */
    lastUsage: UsageInfo | null
    /** 마지막 모델 정보 */
    lastModel: string | null

    // ── actions ──
    setMessages: (messages: DaonMessage[]) => void
    appendMessage: (msg: DaonMessage) => void
    updateMessage: (id: string, patch: Partial<DaonMessage>) => void
    appendToMessageText: (id: string, text: string) => void
    setStreamStatus: (status: StreamStatus, streamId?: string | null) => void
    setLastUsage: (usage: UsageInfo | null) => void
    setLastModel: (model: string | null) => void
    clearMessages: () => void
}

export const useChatStore = create<ChatState>((set) => ({
    messages: [],
    streamStatus: "idle",
    streamId: null,
    lastUsage: null,
    lastModel: null,

    setMessages: (messages) => set({ messages }),

    appendMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),

    updateMessage: (id, patch) =>
        set((s) => ({
            messages: s.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)),
        })),

    appendToMessageText: (id, text) =>
        set((s) => ({
            messages: s.messages.map((m) => (m.id === id ? { ...m, text: (m.text ?? "") + text } : m)),
        })),

    setStreamStatus: (streamStatus, streamId) =>
        set((s) => ({ streamStatus, streamId: streamId === undefined ? s.streamId : streamId })),

    setLastUsage: (lastUsage) => set({ lastUsage }),
    setLastModel: (lastModel) => set({ lastModel }),
    clearMessages: () => set({ messages: [], lastUsage: null, streamStatus: "idle", streamId: null }),
}))

/** 새 메시지 ID 생성 */
export function nextMessageId(): string {
    return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}
