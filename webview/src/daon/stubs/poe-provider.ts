/**
 * `ai-sdk-provider-poe/code` 스텁 (DAON).
 *
 * vendored roo-types/providers/poe.ts가 이 패키지에서
 * poeDefaultModelId / POE_DEFAULT_BASE_URL / getPoeDefaultModelInfo / PoeDefaultModelInfo를
 * re-export한다. DAON은 Poe 프로바이더를 사용하지 않으므로,
 * 컴파일과 설정 화면 렌더링에 필요한 최소 값만 제공한다.
 *
 * vite.config.ts alias와 tsconfig paths로 주입된다.
 */

export const poeDefaultModelId = "claude-sonnet-4"

export const POE_DEFAULT_BASE_URL = "https://api.poe.com/bot/"

export interface PoeDefaultModelInfo {
    maxTokens?: number
    contextWindow?: number
    supportsImages?: boolean
    supportsPromptCache: boolean
    inputPrice?: number
    outputPrice?: number
    cacheWritesPrice?: number
    cacheReadsPrice?: number
    description?: string
}

export function getPoeDefaultModelInfo(_modelId?: string): PoeDefaultModelInfo {
    return {
        maxTokens: 8192,
        contextWindow: 200_000,
        supportsImages: true,
        supportsPromptCache: true,
        inputPrice: 3,
        outputPrice: 15,
        cacheWritesPrice: 3.75,
        cacheReadsPrice: 0.3,
        description: "Poe default model (DAON stub)",
    }
}
