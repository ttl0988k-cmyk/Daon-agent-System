/**
 * Parent Bridge — 임베드(iframe) 모드에서 DAON shell(부모 창)로
 * 특정 이벤트를 전달하는 단방향 브리지.
 *
 * 배경:
 *   - Roo ChatView가 DAON index.html의 #chatModeContent 안 iframe에서 돌 때,
 *     SSE 이벤트는 iframe 내부 DaonBridge/EventBus로만 흐른다.
 *   - DAON shell의 다이나믹 하네스(harness.js)는 부모 창의 전역 함수
 *     (appendCardLog / updateCardStatus / switchMode)로 동작한다.
 *   - 따라서 채팅 에이전트가 execute_dynamic_harness 도구를 호출하거나
 *     agent_log 이벤트가 오면 부모 창으로 postMessage 해야 하네스 연동이
 *     기존 chat.js 경로와 동일하게 유지된다.
 *
 * 이 파일은 Roo 타입을 일절 참조하지 않는다.
 */

import { bus } from "./bus/EventBus"

const PARENT_ORIGIN = "*" // same-origin 임베드 전용 (DAON server가 양쪽을 서빙)
const SOURCE_TAG = "daon-webview"

let installed = false

/**
 * 부모 창 브리지 설치. iframe 내부가 아니면(단독 페이지) 아무 것도 하지 않는다.
 * 여러 번 호출해도 한 번만 실행된다.
 */
export function installParentBridge(): void {
    if (installed) return
    installed = true

    // iframe 밖(단독 로드)에서는 전달 대상이 없다.
    if (window.parent === window) return

    // ── agent_log → 하네스 에이전트 노드 카드 ──
    bus.on("agent_log", (payload) => {
        const p = payload as { agent_id?: string; content?: string; status?: string }
        window.parent.postMessage(
            {
                source: SOURCE_TAG,
                type: "agent_log",
                agent_id: p.agent_id || "harness",
                content: p.content || "",
                status: p.status || "running",
            },
            PARENT_ORIGIN,
        )
    })

    // ── execute_dynamic_harness 도구 시작/완료 → 하네스 탭 전환 ──
    bus.on("tool", (payload) => {
        const p = payload as { name?: string; event?: string; args?: Record<string, unknown> }
        if (p.name !== "execute_dynamic_harness") return
        const started = p.event === "start" || p.event === "tool.started"
        window.parent.postMessage(
            {
                source: SOURCE_TAG,
                type: "harness_tool",
                started,
                task: (p.args && typeof p.args.task === "string" ? p.args.task : "") || "",
            },
            PARENT_ORIGIN,
        )
    })
}
