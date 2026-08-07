/**
 * Session Store — 세션 목록/활성 세션/설정 상태.
 * Roo의 ExtensionStateContext가 담당하던 전역 상태를 여기서 대체한다.
 */

import { create } from "zustand"
import type { SessionListItem, SessionResponse } from "../types"

interface SessionState {
    sessions: SessionListItem[]
    activeSessionId: string | null
    activeSession: SessionResponse | null
    activeModelId: string
    activeWorkspace: string
    /** 사용 가능한 모델 그룹 */
    modelGroups: Array<{ provider: string; models: Array<{ id: string; label: string; type?: string }> }>
    /** 현재 모드 (architect/code/ask 등) */
    mode: string

    setSessions: (sessions: SessionListItem[]) => void
    setActiveSession: (session: SessionResponse | null) => void
    setActiveSessionId: (id: string | null) => void
    setActiveModelId: (id: string) => void
    setActiveWorkspace: (ws: string) => void
    setModelGroups: (groups: SessionState["modelGroups"]) => void
    setMode: (mode: string) => void
}

export const useSessionStore = create<SessionState>((set) => ({
    sessions: [],
    activeSessionId: null,
    activeSession: null,
    activeModelId: "",
    activeWorkspace: "",
    modelGroups: [],
    mode: "code",

    setSessions: (sessions) => set({ sessions }),
    setActiveSession: (activeSession) =>
        set({ activeSession, activeSessionId: activeSession?.session_id ?? null }),
    setActiveSessionId: (activeSessionId) => set({ activeSessionId }),
    setActiveModelId: (activeModelId) => set({ activeModelId }),
    setActiveWorkspace: (activeWorkspace) => set({ activeWorkspace }),
    setModelGroups: (modelGroups) => set({ modelGroups }),
    setMode: (mode) => set({ mode }),
}))
