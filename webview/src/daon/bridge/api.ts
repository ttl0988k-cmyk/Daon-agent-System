/**
 * DAON REST API 클라이언트.
 * server.py / api/routes 엔드포인트와 1:1 대응.
 */

const BASE = "" // same-origin (server.py가 정적 서빙)

async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(BASE + path, {
        headers: { "Content-Type": "application/json" },
        ...init,
    })
    if (!res.ok) {
        let msg = `HTTP ${res.status}`
        try {
            const body = await res.json()
            if (body.error) msg = body.error
        } catch {
            /* ignore */
        }
        throw new Error(msg)
    }
    return res.json() as Promise<T>
}

function post<T>(path: string, body: unknown): Promise<T> {
    return request<T>(path, { method: "POST", body: JSON.stringify(body) })
}

// ── Chat ─────────────────────────────────────────────────────────────────────

export interface ChatStartResponse {
    stream_id: string
    session_id: string
}

export interface ChatStartParams {
    session_id: string
    message: string
    model?: string
    workspace?: string
    attachments?: string[]
    planning_mode?: boolean
    open_tabs?: unknown[]
    media_options?: Record<string, unknown>
}

export const chatApi = {
    /** POST /api/chat/start → {stream_id, session_id} */
    start(params: ChatStartParams): Promise<ChatStartResponse> {
        return post<ChatStartResponse>("/api/chat/start", params)
    },

    /** POST /api/chat/cancel */
    cancel(stream_id: string): Promise<{ ok: boolean; cancelled: boolean }> {
        return post<{ ok: boolean; cancelled: boolean }>("/api/chat/cancel", { stream_id })
    },

    /** GET /api/chat/stream/status?stream_id= */
    async streamStatus(stream_id: string): Promise<{ active: boolean }> {
        return request<{ active: boolean }>(`/api/chat/stream/status?stream_id=${encodeURIComponent(stream_id)}`)
    },

    /** SSE 스트림 URL 생성 */
    streamUrl(stream_id: string): string {
        return `${BASE}/api/chat/stream?stream_id=${encodeURIComponent(stream_id)}`
    },
}

// ── Sessions ─────────────────────────────────────────────────────────────────

export const sessionApi = {
    /** GET /api/sessions */
    list(): Promise<{ sessions: import("../types").SessionListItem[] }> {
        return request("/api/sessions")
    },

    /** GET /api/session?session_id= */
    get(session_id: string): Promise<{ session: import("../types").SessionResponse }> {
        return request(`/api/session?session_id=${encodeURIComponent(session_id)}`)
    },

    /** POST /api/session/new */
    create(body?: Record<string, unknown>): Promise<import("../types").SessionResponse> {
        return post("/api/session/new", body ?? {})
    },

    /** POST /api/session/rename */
    rename(session_id: string, title: string): Promise<{ ok: boolean }> {
        return post("/api/session/rename", { session_id, title })
    },

    /** POST /api/session/delete */
    remove(session_id: string): Promise<{ ok: boolean }> {
        return post("/api/session/delete", { session_id })
    },

    /** POST /api/session/clear */
    clear(session_id: string): Promise<{ ok: boolean }> {
        return post("/api/session/clear", { session_id })
    },
}

// ── Approval ─────────────────────────────────────────────────────────────────

export const approvalApi = {
    /** GET /api/approval/pending?session_id= */
    async pending(session_id: string): Promise<Record<string, unknown>> {
        return request(`/api/approval/pending?session_id=${encodeURIComponent(session_id)}`)
    },

    /** POST /api/approval/respond */
    respond(body: Record<string, unknown>): Promise<Record<string, unknown>> {
        return post("/api/approval/respond", body)
    },

    /** POST /api/approval/approve */
    approve(body: Record<string, unknown>): Promise<Record<string, unknown>> {
        return post("/api/approval/approve", body)
    },

    /** POST /api/approval/reject */
    reject(body: Record<string, unknown>): Promise<Record<string, unknown>> {
        return post("/api/approval/reject", body)
    },
}

// ── Models / Settings / Modes ────────────────────────────────────────────────

export const configApi = {
    /** GET /api/models */
    models(): Promise<{ groups: Array<{ provider: string; models: Array<{ id: string; label: string; type?: string }> }> }> {
        return request("/api/models")
    },

    /** GET /api/settings */
    settings(): Promise<Record<string, unknown>> {
        return request("/api/settings")
    },

    /** GET /api/modes */
    modes(): Promise<Record<string, unknown>> {
        return request("/api/modes")
    },

    /** POST /api/mode */
    setMode(body: Record<string, unknown>): Promise<Record<string, unknown>> {
        return post("/api/mode", body)
    },
}
