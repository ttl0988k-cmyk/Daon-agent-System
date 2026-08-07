/**
 * DaonBridge — DAON 백엔드와 UI를 잇는 유일한 통로.
 *
 * - EventSource(SSE)를 래핑하여 원본 이벤트를 EventBus로 발행
 * - REST 호출(send/cancel/approval)을 담당
 * - 재연결, idle 워치독, 스트림 생명주기 관리
 *
 * 이 계층은 Roo 타입을 일절 알지 못한다.
 */

import { bus } from "../bus/EventBus"
import { chatApi, type ChatStartParams } from "./api"
import { TERMINAL_EVENTS, type DaonEventName } from "../types"

/** EventSource에 리스너를 등록할 전체 이벤트 목록 */
const SSE_EVENTS: DaonEventName[] = [
    "token",
    "reasoning",
    "tool",
    "terminal_output",
    "job",
    "file_edit",
    "file_edit_done",
    "diff_preview",
    "approval",
    "media_result",
    "heartbeat",
    "model_info",
    "model_fallback",
    "compressed",
    "speak",
    "done",
    "cancel",
    "error",
    "apperror",
    "notice",
    "debate_token",
    "debate_status",
    "debate_message_done",
    "agent_log",
    "patch_warning",
]

export type StreamPhase = "idle" | "connecting" | "streaming" | "closing"

export interface BridgeState {
    phase: StreamPhase
    streamId: string | null
    sessionId: string | null
}

type StateListener = (state: BridgeState) => void

class DaonBridgeImpl {
    private es: EventSource | null = null
    private state: BridgeState = { phase: "idle", streamId: null, sessionId: null }
    private stateListeners = new Set<StateListener>()

    // idle 워치독: heartbeat/token 없이 너무 오래되면 연결 이상 감지
    private idleTimer: ReturnType<typeof setTimeout> | null = null
    private idleExtensions = 0
    private static readonly IDLE_BASE_MS = 30_000
    private static readonly IDLE_MAX_EXTENSIONS = 20

    // ── 상태 구독 ─────────────────────────────────────────────────────────────

    getState(): BridgeState {
        return { ...this.state }
    }

    onStateChange(listener: StateListener): () => void {
        this.stateListeners.add(listener)
        return () => this.stateListeners.delete(listener)
    }

    private setState(patch: Partial<BridgeState>) {
        this.state = { ...this.state, ...patch }
        for (const l of this.stateListeners) {
            try {
                l(this.getState())
            } catch (e) {
                console.error("[DaonBridge] state listener error:", e)
            }
        }
    }

    // ── 메시지 전송 (시작) ────────────────────────────────────────────────────

    /**
     * 메시지 전송: POST /api/chat/start → SSE 연결.
     * 기존 chat.js의 sendMessage() 흐름과 동일.
     */
    async sendMessage(params: ChatStartParams): Promise<string> {
        // 같은 세션에 진행 중 스트림이 있으면 먼저 정리
        this.disconnect()

        this.setState({ phase: "connecting", sessionId: params.session_id })
        try {
            const res = await chatApi.start(params)
            this.connect(res.stream_id)
            return res.stream_id
        } catch (e) {
            this.setState({ phase: "idle" })
            throw e
        }
    }

    /** 진행 중 스트림 취소: POST /api/chat/cancel */
    async cancel(): Promise<boolean> {
        const { streamId } = this.state
        if (!streamId) return false
        try {
            const res = await chatApi.cancel(streamId)
            return res.cancelled
        } catch (e) {
            console.error("[DaonBridge] cancel failed:", e)
            return false
        }
    }

    // ── SSE 연결 관리 ─────────────────────────────────────────────────────────

    /** 기존 스트림 재연결 (세션 전환 후 진행 중 스트림 복원 등) */
    connect(streamId: string): void {
        this.disconnect()
        this.setState({ phase: "connecting", streamId })

        const es = new EventSource(chatApi.streamUrl(streamId))
        this.es = es

        for (const eventName of SSE_EVENTS) {
            es.addEventListener(eventName, (ev: MessageEvent) => {
                let data: unknown = {}
                try {
                    data = ev.data ? JSON.parse(ev.data) : {}
                } catch {
                    data = { raw: ev.data }
                }
                // 원본 이벤트를 그대로 버스에 발행 (해석은 Normalizer가 담당)
                bus.emit(eventName, data as never)

                // 종료 이벤트 → 연결 정리
                if (TERMINAL_EVENTS.has(eventName)) {
                    this.finish()
                } else if (eventName !== "heartbeat") {
                    this.resetIdleTimer()
                } else {
                    this.resetIdleTimer()
                }
            })
        }

        es.onopen = () => {
            if (this.es === es) {
                this.setState({ phase: "streaming" })
                this.resetIdleTimer()
            }
        }

        es.onerror = () => {
            // EventSource는 자동 재연결을 시도한다.
            // 서버는 완료/취소된 스트림의 재연결에 캐시된 done/cancel을 응답한다.
            // 여기서는 idle 워치독만 연장하고, 실제 종료는 이벤트로 판단한다.
            if (this.es === es) {
                console.warn("[DaonBridge] SSE connection error, EventSource will auto-retry")
            }
        }
    }

    /** 스트림 종료 후 정리 */
    private finish(): void {
        this.clearIdleTimer()
        const es = this.es
        this.es = null
        if (es) {
            // 약간의 지연 후 close: 서버가 마지막 이벤트를 flush할 시간 확보
            setTimeout(() => es.close(), 0)
        }
        this.setState({ phase: "idle", streamId: null })
    }

    /** 외부에서 강제 연결 해제 (세션 전환 등) */
    disconnect(): void {
        this.clearIdleTimer()
        const es = this.es
        this.es = null
        if (es) es.close()
        if (this.state.phase !== "idle") {
            this.setState({ phase: "idle", streamId: null })
        }
    }

    // ── idle 워치독 ──────────────────────────────────────────────────────────

    private resetIdleTimer(): void {
        this.clearIdleTimer()
        if (this.state.phase !== "streaming" && this.state.phase !== "connecting") return
        if (this.idleExtensions >= DaonBridgeImpl.IDLE_MAX_EXTENSIONS) return
        this.idleExtensions++
        this.idleTimer = setTimeout(() => {
            console.warn("[DaonBridge] idle timeout — checking stream status")
            const { streamId } = this.state
            if (!streamId) return
            chatApi
                .streamStatus(streamId)
                .then((res) => {
                    if (!res.active) {
                        // 서버에 스트림이 없는데 이벤트도 안 왔다 → 비정상 종료
                        bus.emit("apperror", { message: "연결이 끊어졌습니다. 스트림이 서버에 없습니다." })
                        this.finish()
                    } else {
                        // 아직 활성 → 타이머 재시작
                        this.idleExtensions = Math.max(0, this.idleExtensions - 5)
                        this.resetIdleTimer()
                    }
                })
                .catch(() => {
                    this.resetIdleTimer()
                })
        }, DaonBridgeImpl.IDLE_BASE_MS)
    }

    private clearIdleTimer(): void {
        if (this.idleTimer) {
            clearTimeout(this.idleTimer)
            this.idleTimer = null
        }
        this.idleExtensions = 0
    }
}

/** 앱 전역 싱글톤 브리지 */
export const bridge = new DaonBridgeImpl()
