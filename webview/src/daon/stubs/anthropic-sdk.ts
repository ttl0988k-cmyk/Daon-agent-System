/**
 * `@anthropic-ai/sdk` 스텁 (DAON).
 *
 * vendored roo-shared/tools.ts가 `import { Anthropic } from "@anthropic-ai/sdk"`
 * 형태로 import하지만, 오직 타입 위치에서만 사용한다:
 *   export type ToolResponse = string | Array<Anthropic.TextBlockParam | Anthropic.ImageBlockParam>
 *
 * 실제 SDK(수십 MB)를 설치하는 대신 필요한 타입만 선언한다.
 * vite.config.ts alias와 tsconfig paths로 주입된다.
 */

export namespace Anthropic {
    export interface TextBlockParam {
        type: "text"
        text: string
    }

    export interface ImageBlockParam {
        type: "image"
        source: {
            type: "base64"
            media_type: "image/jpeg" | "image/png" | "image/gif" | "image/webp"
            data: string
        }
    }
}

export default Anthropic
