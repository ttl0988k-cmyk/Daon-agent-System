/**
 * Approval Store — 대기 중 승인 요청 관리.
 * approval / diff_preview 이벤트와 /api/approval/* 엔드포인트를 다룬다.
 */

import { create } from "zustand"

export type ApprovalKind = "architect_diff" | "dangerous_command" | "generic"

export interface PendingApproval {
    /** 승인 고유 키 (preview_id 또는 생성 키) */
    key: string
    kind: ApprovalKind
    previewId?: string
    filePath?: string
    message?: string
    /** 원본 승인 페이로드 */
    raw: Record<string, unknown>
    ts: number
}

interface ApprovalState {
    /** 대기 중 승인 목록 (key → PendingApproval) */
    pending: Record<string, PendingApproval>

    addApproval: (a: PendingApproval) => void
    removeApproval: (key: string) => void
    clear: () => void
}

export const useApprovalStore = create<ApprovalState>((set) => ({
    pending: {},

    addApproval: (a) => set((s) => ({ pending: { ...s.pending, [a.key]: a } })),

    removeApproval: (key) =>
        set((s) => {
            const next = { ...s.pending }
            delete next[key]
            return { pending: next }
        }),

    clear: () => set({ pending: {} }),
}))
