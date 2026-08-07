/**
 * Typed Event Bus — DAON SSE 원본 이벤트를 그대로 발행/구독.
 *
 * 설계 원칙 #3: SSE 이벤트를 그대로 살린다.
 * Bus는 이벤트 의미를 해석하지 않고 그대로 전달만 한다.
 * 해석은 Normalizer의 몫이다.
 */

import type { DaonEventMap, DaonEventName } from "../types"

type Handler<T> = (payload: T) => void

export class EventBus {
    private handlers = new Map<string, Set<Handler<unknown>>>()

    /** 이벤트 구독. 해제 함수 반환. */
    on<K extends DaonEventName>(event: K, handler: Handler<DaonEventMap[K]>): () => void {
        let set = this.handlers.get(event)
        if (!set) {
            set = new Set()
            this.handlers.set(event, set)
        }
        set.add(handler as Handler<unknown>)
        return () => {
            set!.delete(handler as Handler<unknown>)
            if (set!.size === 0) this.handlers.delete(event)
        }
    }

    /** 모든 이벤트 구독 (디버깅/로깅용). */
    onAny(handler: (event: DaonEventName, payload: unknown) => void): () => void {
        const key = "__any__"
        let set = this.handlers.get(key)
        if (!set) {
            set = new Set()
            this.handlers.set(key, set)
        }
        const wrapped = handler as unknown as Handler<unknown>
        set.add(wrapped)
        return () => {
            set!.delete(wrapped)
            if (set!.size === 0) this.handlers.delete(key)
        }
    }

    /** 이벤트 발행. */
    emit<K extends DaonEventName>(event: K, payload: DaonEventMap[K]): void {
        const set = this.handlers.get(event)
        if (set) {
            for (const h of set) {
                try {
                    h(payload)
                } catch (e) {
                    console.error(`[EventBus] handler error for "${event}":`, e)
                }
            }
        }
        const anySet = this.handlers.get("__any__")
        if (anySet) {
            for (const h of anySet) {
                try {
                    ; (h as unknown as (event: DaonEventName, payload: unknown) => void)(event, payload)
                } catch (e) {
                    console.error("[EventBus] onAny handler error:", e)
                }
            }
        }
    }

    /** 모든 구독 해제. */
    clear(): void {
        this.handlers.clear()
    }
}

/** 앱 전역 싱글톤 버스 */
export const bus = new EventBus()
