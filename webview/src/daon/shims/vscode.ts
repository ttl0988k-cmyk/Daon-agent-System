/**
 * vscode shim — Roo의 `@src/utils/vscode`를 대체.
 * Vite alias로 이 파일이 `@src/utils/vscode` 자리에 들어간다.
 *
 * Roo 컴포넌트는 vscode.postMessage(message)를 호출하는데,
 * 여기서 message.type을 해석해 DAON REST/SSE 호출로 변환한다.
 * Roo 컴포넌트 파일은 수정하지 않는다.
 *
 * 주의: 이 파일은 Roo의 WebviewMessage 타입을 느슨하게(any) 받아서
 * DAON API로 매핑한다. 매핑 표는 docs/ROO_CHAT_PORT_PLAN.md §2.3 참고.
 */

import { bridge } from "../bridge/DaonBridge"
import { approvalApi, sessionApi } from "../bridge/api"
import { useChatStore } from "../stores/chatStore"
import { useSessionStore } from "../stores/sessionStore"
import { useApprovalStore } from "../stores/approvalStore"

interface WebviewMessageLike {
    type: string
    [key: string]: unknown
}

class DaonVSCodeAPI {
    /**
     * Roo 컴포넌트가 확장 프로그램으로 보내는 메시지를 DAON 호출로 변환.
     */
    postMessage(message: WebviewMessageLike): void {
        const type = message?.type
        switch (type) {
            // ── 사용자 응답 (승인 / follow-up / 텍스트 응답) ──
            case "askResponse": {
                this.handleAskResponse(message)
                break
            }

            // ── 승인 버튼 (Roo의 primary/secondary button) ──
            case "primaryButtonClick":
            case "secondaryButtonClick": {
                this.handleApprovalButton(message)
                break
            }

            // ── 새 태스크 / 세션 ──
            case "clearTask":
            case "newTask": {
                void this.handleNewTask(message)
                break
            }

            // ── 태스크 취소 ──
            case "cancelTask": {
                void bridge.cancel()
                break
            }

            // ── 모드 전환 ──
            case "switchMode": {
                const mode = message.mode as string | undefined
                if (mode) useSessionStore.getState().setMode(mode)
                break
            }

            // ── 파일 열기 (에디터 연동은 DAON 기존 모듈이 담당) ──
            case "openFile": {
                console.info("[daon] openFile:", message.path)
                break
            }

            default:
                // 미매핑 메시지는 로그만 (Roo 기능 중 DAON 미지원 영역)
                console.debug("[daon] unhandled postMessage:", type, message)
        }
    }

    /**
     * askResponse: 사용자가 ask(승인/질문)에 응답한 경우.
     * - 대기 중 승인이 있으면 /api/approval/respond 로 전달
     * - 없으면 일반 채팅 메시지로 전송
     */
    private handleAskResponse(message: WebviewMessageLike): void {
        const text = (message.text as string | undefined) ?? ""
        const pending = useApprovalStore.getState().pending
        const pendingKeys = Object.keys(pending)

        if (pendingKeys.length > 0) {
            const approval = pending[pendingKeys[0]]
            const sessionId = useSessionStore.getState().activeSessionId
            void approvalApi
                .respond({
                    session_id: sessionId,
                    preview_id: approval.previewId,
                    response: message.askResponse,
                    text,
                })
                .then(() => {
                    useApprovalStore.getState().removeApproval(approval.key)
                })
                .catch((e) => console.error("[daon] approval respond failed:", e))
            return
        }

        // 승인 없으면 텍스트를 새 메시지로 전송
        if (text.trim()) {
            void this.sendChatMessage(text)
        }
    }

    /**
     * primary/secondary button: 승인/거부 버튼.
     */
    private handleApprovalButton(message: WebviewMessageLike): void {
        const pending = useApprovalStore.getState().pending
        const pendingKeys = Object.keys(pending)
        if (pendingKeys.length === 0) return

        const approval = pending[pendingKeys[0]]
        const isApprove = message.type === "primaryButtonClick"
        const fn = isApprove ? approvalApi.approve : approvalApi.reject
        void fn({
            session_id: useSessionStore.getState().activeSessionId,
            preview_id: approval.previewId,
        })
            .then(() => {
                useApprovalStore.getState().removeApproval(approval.key)
            })
            .catch((e) => console.error("[daon] approval button failed:", e))
    }

    /**
     * 새 세션 생성 후 전환.
     * Roo의 ChatView는 첫 메시지를 `{type:"newTask", text, images}`로 보내므로
     * 세션 생성 후 text를 그대로 전송해야 대화가 시작된다.
     */
    private async handleNewTask(message: WebviewMessageLike): Promise<void> {
        try {
            const session = await sessionApi.create()
            useSessionStore.getState().setActiveSession(session)
            useChatStore.getState().clearMessages()

            const text = (message.text as string | undefined) ?? ""
            if (text.trim()) {
                await this.sendChatMessage(text)
            }
        } catch (e) {
            console.error("[daon] new task failed:", e)
        }
    }

    /** 일반 채팅 메시지 전송 */
    private async sendChatMessage(text: string): Promise<void> {
        const { activeSessionId, activeModelId, activeWorkspace } = useSessionStore.getState()
        if (!activeSessionId) {
            console.warn("[daon] no active session, cannot send message")
            return
        }
        useChatStore.getState().setStreamStatus("connecting")
        try {
            await bridge.sendMessage({
                session_id: activeSessionId,
                message: text,
                model: activeModelId || undefined,
                workspace: activeWorkspace || undefined,
            })
            useChatStore.getState().setStreamStatus("streaming")
        } catch (e) {
            console.error("[daon] send message failed:", e)
            useChatStore.getState().setStreamStatus("idle")
        }
    }

    /** 브라우저 환경에서는 localStorage로 상태 영속화 */
    getState(): unknown | undefined {
        const state = localStorage.getItem("daonWebviewState")
        return state ? JSON.parse(state) : undefined
    }

    setState<T>(newState: T): T {
        localStorage.setItem("daonWebviewState", JSON.stringify(newState))
        return newState
    }
}

/** Roo가 기대하는 싱글톤 export */
export const vscode = new DaonVSCodeAPI()
