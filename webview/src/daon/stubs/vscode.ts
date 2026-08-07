/**
 * `vscode` 모듈 스텁 (DAON).
 *
 * vendored roo-shared/modes.ts, roo-shared/vsCodeSelectorUtils.ts,
 * roo/components/settings/providers/VSCodeLM.tsx가 `vscode`를 import하지만
 * 전부 타입 위치(type position)에서만 사용한다. DAON webview는 브라우저에서
 * 동작하므로 실제 VS Code API는 존재하지 않고, 컴파일을 만족시키는
 * 최소 타입 정의만 제공한다.
 *
 * vite.config.ts의 alias(/^vscode$/)와 tsconfig paths("vscode")로 주입된다.
 */

/** VS Code Language Model 채팅 셀렉터 (vscode.LanguageModelChatSelector) */
export interface LanguageModelChatSelector {
    vendor?: string
    family?: string
    version?: string
    id?: string
}

/** vscode.Memento 최소 스텁 */
export interface Memento {
    get<T>(key: string): Promise<T | undefined>
    get<T>(key: string, defaultValue: T): Promise<T>
    update(key: string, value: unknown): Promise<void>
    keys(): readonly string[]
}

/** vscode.ExtensionContext 최소 스텁 (modes.ts getAllModesWithPrompts에서 사용) */
export interface ExtensionContext {
    globalState: Memento
    workspaceState: Memento
    extensionPath: string
    extensionUri: { toString(): string }
}

/** vscode.Uri 최소 스텁 */
export interface Uri {
    toString(): string
    fsPath: string
}
