/**
 * DAON 런타임 부트스트랩.
 *
 * shim ExtensionStateContext가 마운트될 때 한 번만 호출된다.
 *   - Normalizer 설치 (SSE 이벤트 → Store)
 *   - 세션/모델 목록 초기 로드
 *   - 세션 전환 시 히스토리 복원 (REST → DaonMessage[])
 *
 * 이 파일은 Roo 타입을 일절 참조하지 않는다.
 */

import { configApi, sessionApi } from "./bridge/api"
import { bridge } from "./bridge/DaonBridge"
import { installNormalizer } from "./normalizer/EventNormalizer"
import { installParentBridge } from "./parent-bridge"
import { useApprovalStore } from "./stores/approvalStore"
import { nextMessageId, useChatStore, type DaonMessage } from "./stores/chatStore"
import { useSessionStore } from "./stores/sessionStore"
import type { SessionMessage } from "./types"

const SESSION_ID_KEY = "daonActiveSessionId"

let installed = false

/**
 * DAON 런타임 설치. 여러 번 호출해도 한 번만 실행된다.
 */
export function installDaonRuntime(): void {
    if (installed) return
    installed = true

    installNormalizer()

    // 임베드(iframe) 모드: 하네스 연동 이벤트를 DAON shell(부모 창)로 전달.
    // 단독 페이지 로드 시에는 내부에서 no-op.
    installParentBridge()

    // 세션 전환 감시 → 히스토리 다시 로드
    useSessionStore.subscribe((state, prev) => {
        if (state.activeSessionId === prev.activeSessionId) return
        if (state.activeSessionId) {
            try {
                localStorage.setItem(SESSION_ID_KEY, state.activeSessionId)
            } catch {
                /* ignore */
            }
        }
        void loadSessionHistory(state.activeSessionId)
    })

    void bootstrap()
}

/** 초기 로드: 모델 목록 + 세션 목록 → 활성 세션 선택(또는 생성) */
async function bootstrap(): Promise<void> {
    // 모델 목록 (실패해도 치명적이지 않음)
    configApi
        .models()
        .then((res) => useSessionStore.getState().setModelGroups(res.groups || []))
        .catch((e) => console.warn("[daon] load models failed:", e))

    try {
        const res = await sessionApi.list()
        const sessions = res.sessions || []
        useSessionStore.getState().setSessions(sessions)

        let savedId: string | null = null
        try {
            savedId = localStorage.getItem(SESSION_ID_KEY)
        } catch {
            /* ignore */
        }
        const found = savedId ? sessions.find((s) => s.session_id === savedId) : undefined
        const target = found || sessions[0]

        if (target) {
            // subscribe가 히스토리 로드를 트리거한다
            useSessionStore.getState().setActiveSessionId(target.session_id)
        } else {
            const session = await sessionApi.create()
            useSessionStore.getState().setActiveSession(session)
        }
    } catch (e) {
        console.error("[daon] bootstrap failed:", e)
        // 세션 목록 로드 실패 시 새 세션 생성 시도
        try {
            const session = await sessionApi.create()
            useSessionStore.getState().setActiveSession(session)
        } catch {
            /* 서버 오프라인 — 빈 상태로 시작 */
        }
    }
}

/**
 * 세션 히스토리를 REST로 로드해 chatStore에 복원.
 * 응답 도착 시점에 이미 새 스트림이 시작됐거나 세션이 바뀌었으면 덮어쓰지 않는다.
 */
async function loadSessionHistory(sessionId: string | null): Promise<void> {
    // 진행 중 스트림이 있으면 서버 측 취소 후 연결 정리
    if (useChatStore.getState().streamId) {
        void bridge.cancel()
    }
    bridge.disconnect()
    useChatStore.getState().clearMessages()
    useApprovalStore.getState().clear()

    if (!sessionId) return

    try {
        const res = await sessionApi.get(sessionId)
        // 레이스 가드: 그 사이 스트림이 시작됐거나 다른 세션으로 전환됨
        if (useChatStore.getState().streamStatus !== "idle") return
        if (useSessionStore.getState().activeSessionId !== sessionId) return

        const msgs = res.session?.messages || []
        useChatStore.getState().setMessages(historyToDaonMessages(msgs))
    } catch (e) {
        console.error("[daon] load session history failed:", e)
    }
}

/** <think>...</think> 블록 분리용 정규식 */
const THINK_RE = /<think>([\s\S]*?)<\/think>/g

/**
 * 서버 세션 메시지(SessionMessage[])를 DaonMessage[]로 변환.
 * - user → kind "text" (role user)
 * - assistant → <think> 블록은 reasoning, 나머지는 text
 * - system → kind "info"
 */
export function historyToDaonMessages(messages: SessionMessage[]): DaonMessage[] {
    const out: DaonMessage[] = []
    // 히스토리 메시지는 과거 시점의 단조 증가 ts 부여
    let ts = Date.now() - messages.length * 10 - 1000

    const push = (partial: Omit<DaonMessage, "id" | "ts">) => {
        ts += 10
        out.push({ ...partial, id: nextMessageId(), ts })
    }

    for (const sm of messages) {
        const content = typeof sm.content === "string" ? sm.content : ""
        if (!content.trim()) continue

        if (sm.role === "user") {
            push({ role: "user", kind: "text", text: content })
            continue
        }

        if (sm.role === "system") {
            push({ role: "system", kind: "info", text: content })
            continue
        }

        // assistant: <think> 블록을 reasoning으로 분리
        let lastIndex = 0
        THINK_RE.lastIndex = 0
        let match: RegExpExecArray | null
        while ((match = THINK_RE.exec(content)) !== null) {
            const before = content.slice(lastIndex, match.index).trim()
            if (before) push({ role: "assistant", kind: "text", text: before })
            const think = (match[1] || "").trim()
            if (think) push({ role: "assistant", kind: "reasoning", text: think })
            lastIndex = THINK_RE.lastIndex
        }
        const tail = content.slice(lastIndex).trim()
        if (tail) push({ role: "assistant", kind: "text", text: tail })
    }

    return out
}
