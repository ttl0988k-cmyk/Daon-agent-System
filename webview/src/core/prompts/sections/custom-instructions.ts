/**
 * DAON 스텁 — Roo 원본 `src/core/prompts/sections/custom-instructions.ts` 대체.
 *
 * Roo 원본은 확장 호스트에서 .roo/rules 파일·.clinerules 등을 읽어들여
 * 커스텀 지시사항을 조립하지만, DAON 웹뷰에서는 파일시스템 접근이 없다.
 * 따라서 모드별 base 지시사항과 전역 지시사항을 단순 결합하는 형태로 대체한다.
 *
 * 이 파일은 `webview/src/roo-shared/modes.ts`의 상대 import
 * `../core/prompts/sections/custom-instructions` 를 만족시키기 위해
 * `webview/src/core/...` 위치에 둔다. (Roo 원본 파일은 수정하지 않는다.)
 */

export async function addCustomInstructions(
    baseCustomInstructions: string,
    globalCustomInstructions: string,
    _cwd: string,
    _modeSlug: string,
    _options?: { language?: string },
): Promise<string> {
    const parts: string[] = []

    if (globalCustomInstructions && globalCustomInstructions.trim().length > 0) {
        parts.push(globalCustomInstructions.trim())
    }

    if (baseCustomInstructions && baseCustomInstructions.trim().length > 0) {
        parts.push(baseCustomInstructions.trim())
    }

    return parts.join("\n\n")
}
