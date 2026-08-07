/**
 * Tool Store — 도구 실행 상태 관리.
 * tool / terminal_output / job / file_edit 이벤트를 집계한다.
 */

import { create } from "zustand"

export type ToolStatus = "running" | "completed" | "error"

export interface ToolRun {
    /** 실행 고유 키 (tool name + 시작 ts) */
    key: string
    name: string
    status: ToolStatus
    preview?: string
    args?: Record<string, unknown>
    /** 터미널 출력 누적 버퍼 */
    output: string
    startTs: number
    endTs?: number
    duration?: number
}

interface ToolState {
    /** 진행 중/완료된 도구 실행 map (key → ToolRun) */
    runs: Record<string, ToolRun>
    /** 배치 작업(job) 진행 상태 */
    jobTools: string[]
    jobActive: boolean

    // ── actions ──
    startRun: (key: string, run: ToolRun) => void
    updateRun: (key: string, patch: Partial<ToolRun>) => void
    appendOutput: (key: string, text: string) => void
    /** tool name으로 가장 최근 running 실행 찾기 */
    findRunningByName: (name: string) => string | undefined
    setJob: (tools: string[], active: boolean) => void
    clear: () => void
}

export const useToolStore = create<ToolState>((set, get) => ({
    runs: {},
    jobTools: [],
    jobActive: false,

    startRun: (key, run) => set((s) => ({ runs: { ...s.runs, [key]: run } })),

    updateRun: (key, patch) =>
        set((s) => {
            const existing = s.runs[key]
            if (!existing) return s
            return { runs: { ...s.runs, [key]: { ...existing, ...patch } } }
        }),

    appendOutput: (key, text) =>
        set((s) => {
            const existing = s.runs[key]
            if (!existing) return s
            return { runs: { ...s.runs, [key]: { ...existing, output: existing.output + text } } }
        }),

    findRunningByName: (name) => {
        const { runs } = get()
        // 가장 최근(늦은 startTs) running 실행 반환
        let best: string | undefined
        let bestTs = -1
        for (const [key, run] of Object.entries(runs)) {
            if (run.name === name && run.status === "running" && run.startTs > bestTs) {
                best = key
                bestTs = run.startTs
            }
        }
        return best
    },

    setJob: (jobTools, jobActive) => set({ jobTools, jobActive }),

    clear: () => set({ runs: {}, jobTools: [], jobActive: false }),
}))
