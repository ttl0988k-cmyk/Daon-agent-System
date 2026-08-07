/**
 * ExtensionStateContext shim — Roo의 `@src/context/ExtensionStateContext`를 대체.
 * Vite alias로 이 파일이 원본 자리에 들어간다. Roo 컴포넌트는 수정하지 않는다.
 *
 * 차이점:
 *   - clineMessages  ← DAON chatStore → adapter/viewModel(toClineMessages) 변환 결과
 *   - taskHistory    ← DAON sessionStore.sessions
 *   - mode           ← DAON sessionStore.mode
 *   - currentTaskId  ← DAON sessionStore.activeSessionId
 *   - 나머지 설정 값은 Roo 원본과 동일한 기본값 + 로컬 setState로 동작
 *
 * export 계약(인터페이스/기본값/setter 목록)은 Roo 원본과 완전히 동일하게 유지한다.
 */

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react"

import {
    type ProviderSettings,
    type ProviderSettingsEntry,
    type CustomModePrompts,
    type ModeConfig,
    type ExperimentId,
    type TodoItem,
    type OrganizationAllowList,
    type ExtensionState,
    type SkillMetadata,
    type Command,
    type McpServer,
    type RouterModels,
    type HistoryItem,
    ORGANIZATION_ALLOW_ALL,
    DEFAULT_CHECKPOINT_TIMEOUT_SECONDS,
} from "@roo-code/types"

import { type Mode, defaultModeSlug, defaultPrompts } from "@roo/modes"
import { type CustomSupportPrompts } from "@roo/support-prompt"
import { experimentDefault } from "@roo/experiments"

import { toClineMessages } from "../adapter/viewModel"
import { installDaonRuntime } from "../bootstrap"
import { useApprovalStore } from "../stores/approvalStore"
import { useChatStore } from "../stores/chatStore"
import { useSessionStore } from "../stores/sessionStore"

export interface ExtensionStateContextType extends ExtensionState {
    historyPreviewCollapsed?: boolean // Add the new state property
    didHydrateState: boolean
    showWelcome: boolean
    theme: any
    mcpServers: McpServer[]
    currentCheckpoint?: string
    currentTaskTodos?: TodoItem[] // Initial todos for the current task
    filePaths: string[]
    openedTabs: Array<{ label: string; isActive: boolean; path?: string }>
    commands: Command[]
    organizationAllowList: OrganizationAllowList
    hasOpenedModeSelector: boolean // New property to track if user has opened mode selector
    setHasOpenedModeSelector: (value: boolean) => void // Setter for the new property
    alwaysAllowFollowupQuestions: boolean // New property for follow-up questions auto-approve
    setAlwaysAllowFollowupQuestions: (value: boolean) => void // Setter for the new property
    followupAutoApproveTimeoutMs: number | undefined // Timeout in ms for auto-approving follow-up questions
    setFollowupAutoApproveTimeoutMs: (value: number) => void // Setter for the timeout
    profileThresholds: Record<string, number>
    setProfileThresholds: (value: Record<string, number>) => void
    setApiConfiguration: (config: ProviderSettings) => void
    setCustomInstructions: (value?: string) => void
    setAlwaysAllowReadOnly: (value: boolean) => void
    setAlwaysAllowReadOnlyOutsideWorkspace: (value: boolean) => void
    setAlwaysAllowWrite: (value: boolean) => void
    setAlwaysAllowWriteOutsideWorkspace: (value: boolean) => void
    setAlwaysAllowExecute: (value: boolean) => void
    setAlwaysAllowMcp: (value: boolean) => void
    setAlwaysAllowModeSwitch: (value: boolean) => void
    setAlwaysAllowSubtasks: (value: boolean) => void
    setShowRooIgnoredFiles: (value: boolean) => void
    setEnableSubfolderRules: (value: boolean) => void
    setShowAnnouncement: (value: boolean) => void
    setAllowedCommands: (value: string[]) => void
    setDeniedCommands: (value: string[]) => void
    setAllowedMaxRequests: (value: number | undefined) => void
    setAllowedMaxCost: (value: number | undefined) => void
    setSoundEnabled: (value: boolean) => void
    setSoundVolume: (value: number) => void
    terminalShellIntegrationTimeout?: number
    setTerminalShellIntegrationTimeout: (value: number) => void
    terminalShellIntegrationDisabled?: boolean
    setTerminalShellIntegrationDisabled: (value: boolean) => void
    terminalZdotdir?: boolean
    setTerminalZdotdir: (value: boolean) => void
    setTtsEnabled: (value: boolean) => void
    setTtsSpeed: (value: number) => void
    setEnableCheckpoints: (value: boolean) => void
    checkpointTimeout: number
    setCheckpointTimeout: (value: number) => void
    setWriteDelayMs: (value: number) => void
    terminalOutputPreviewSize?: "small" | "medium" | "large"
    setTerminalOutputPreviewSize: (value: "small" | "medium" | "large") => void
    mcpEnabled: boolean
    setMcpEnabled: (value: boolean) => void
    setCurrentApiConfigName: (value: string) => void
    setListApiConfigMeta: (value: ProviderSettingsEntry[]) => void
    mode: Mode
    setMode: (value: Mode) => void
    setCustomModePrompts: (value: CustomModePrompts) => void
    setCustomSupportPrompts: (value: CustomSupportPrompts) => void
    enhancementApiConfigId?: string
    setEnhancementApiConfigId: (value: string) => void
    setExperimentEnabled: (id: ExperimentId, enabled: boolean) => void
    setAutoApprovalEnabled: (value: boolean) => void
    customModes: ModeConfig[]
    setCustomModes: (value: ModeConfig[]) => void
    setMaxOpenTabsContext: (value: number) => void
    maxWorkspaceFiles: number
    setMaxWorkspaceFiles: (value: number) => void
    awsUsePromptCache?: boolean
    setAwsUsePromptCache: (value: boolean) => void
    maxImageFileSize: number
    setMaxImageFileSize: (value: number) => void
    maxTotalImageSize: number
    setMaxTotalImageSize: (value: number) => void
    pinnedApiConfigs?: Record<string, boolean>
    setPinnedApiConfigs: (value: Record<string, boolean>) => void
    togglePinnedApiConfig: (configName: string) => void
    setHistoryPreviewCollapsed: (value: boolean) => void
    setReasoningBlockCollapsed: (value: boolean) => void
    enterBehavior?: "send" | "newline"
    setEnterBehavior: (value: "send" | "newline") => void
    autoCondenseContext: boolean
    setAutoCondenseContext: (value: boolean) => void
    autoCondenseContextPercent: number
    setAutoCondenseContextPercent: (value: number) => void
    routerModels?: RouterModels
    includeDiagnosticMessages?: boolean
    setIncludeDiagnosticMessages: (value: boolean) => void
    maxDiagnosticMessages?: number
    setMaxDiagnosticMessages: (value: number) => void
    includeTaskHistoryInEnhance?: boolean
    setIncludeTaskHistoryInEnhance: (value: boolean) => void
    includeCurrentTime?: boolean
    setIncludeCurrentTime: (value: boolean) => void
    includeCurrentCost?: boolean
    setIncludeCurrentCost: (value: boolean) => void
    showWorktreesInHomeScreen: boolean
    setShowWorktreesInHomeScreen: (value: boolean) => void
    skills?: SkillMetadata[]
}

export const ExtensionStateContext = createContext<ExtensionStateContextType | undefined>(undefined)

export const mergeExtensionState = (prevState: ExtensionState, newState: Partial<ExtensionState>) => {
    const { customModePrompts: prevCustomModePrompts, experiments: prevExperiments, ...prevRest } = prevState

    const {
        apiConfiguration,
        customModePrompts: newCustomModePrompts,
        customSupportPrompts,
        experiments: newExperiments,
        ...newRest
    } = newState

    const customModePrompts = { ...prevCustomModePrompts, ...(newCustomModePrompts ?? {}) }
    const experiments = { ...prevExperiments, ...(newExperiments ?? {}) }
    const rest = { ...prevRest, ...newRest }

    if (
        newState.clineMessagesSeq !== undefined &&
        prevState.clineMessagesSeq !== undefined &&
        newState.clineMessagesSeq <= prevState.clineMessagesSeq &&
        newState.clineMessages !== undefined
    ) {
        rest.clineMessages = prevState.clineMessages
        rest.clineMessagesSeq = prevState.clineMessagesSeq
    }

    return {
        ...rest,
        apiConfiguration: apiConfiguration ?? prevState.apiConfiguration,
        customModePrompts,
        customSupportPrompts: customSupportPrompts ?? prevState.customSupportPrompts,
        experiments,
    }
}

export const ExtensionStateContextProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    // 설정 값은 Roo 원본과 동일한 기본값으로 로컬 관리한다.
    const [state, setState] = useState<ExtensionState>({
        apiConfiguration: {},
        version: "",
        clineMessages: [],
        taskHistory: [],
        shouldShowAnnouncement: false,
        allowedCommands: [],
        deniedCommands: [],
        soundEnabled: false,
        soundVolume: 0.5,
        ttsEnabled: false,
        ttsSpeed: 1.0,
        enableCheckpoints: true,
        checkpointTimeout: DEFAULT_CHECKPOINT_TIMEOUT_SECONDS, // Default to 15 seconds
        language: "ko", // DAON 기본 언어
        writeDelayMs: 1000,
        terminalShellIntegrationTimeout: 4000,
        mcpEnabled: true,
        currentApiConfigName: "default",
        listApiConfigMeta: [],
        mode: defaultModeSlug,
        customModePrompts: defaultPrompts,
        customSupportPrompts: {},
        experiments: experimentDefault,
        enhancementApiConfigId: "",
        hasOpenedModeSelector: false, // Default to false (not opened yet)
        autoApprovalEnabled: false,
        customModes: [],
        maxOpenTabsContext: 20,
        maxWorkspaceFiles: 200,
        cwd: "",
        showRooIgnoredFiles: true,
        enableSubfolderRules: false,
        renderContext: "sidebar",
        maxReadFileLine: -1,
        maxImageFileSize: 5,
        maxTotalImageSize: 20,
        pinnedApiConfigs: {},
        terminalZshOhMy: false,
        terminalZshP10k: false,
        terminalZdotdir: false,
        historyPreviewCollapsed: false,
        reasoningBlockCollapsed: true, // Default to collapsed
        enterBehavior: "send", // Default: Enter sends, Shift+Enter creates newline
        organizationAllowList: ORGANIZATION_ALLOW_ALL,
        autoCondenseContext: true,
        autoCondenseContextPercent: 100,
        profileThresholds: {},
        codebaseIndexConfig: {
            codebaseIndexEnabled: true,
            codebaseIndexQdrantUrl: "http://localhost:6333",
            codebaseIndexEmbedderProvider: "openai",
            codebaseIndexEmbedderBaseUrl: "",
            codebaseIndexEmbedderModelId: "",
            codebaseIndexSearchMaxResults: undefined,
            codebaseIndexSearchMinScore: undefined,
        },
        codebaseIndexModels: { ollama: {}, openai: {} },
        includeDiagnosticMessages: true,
        maxDiagnosticMessages: 50,
        openRouterImageApiKey: "",
        openRouterImageGenerationSelectedModel: "",
        includeCurrentTime: true,
        includeCurrentCost: true,
        lockApiConfigAcrossModes: false,
    })

    const [theme] = useState<any>(undefined)
    const [filePaths] = useState<string[]>([])
    const [openedTabs] = useState<Array<{ label: string; isActive: boolean; path?: string }>>([])
    const [commands] = useState<Command[]>([])
    const [mcpServers] = useState<McpServer[]>([])
    const [currentCheckpoint] = useState<string>()
    const [skills] = useState<SkillMetadata[]>([])
    const [alwaysAllowFollowupQuestions, setAlwaysAllowFollowupQuestions] = useState(false)
    const [followupAutoApproveTimeoutMs, setFollowupAutoApproveTimeoutMsState] = useState<number | undefined>(
        undefined,
    )
    const [includeTaskHistoryInEnhance, setIncludeTaskHistoryInEnhance] = useState(true)
    const [includeCurrentTime, setIncludeCurrentTime] = useState(true)
    const [includeCurrentCost, setIncludeCurrentCost] = useState(true)

    // ── DAON Store 구독 ────────────────────────────────────────────────────────
    const daonMessages = useChatStore((s) => s.messages)
    const streamStatus = useChatStore((s) => s.streamStatus)
    const lastUsage = useChatStore((s) => s.lastUsage)
    const pendingApprovals = useApprovalStore((s) => s.pending)
    const sessions = useSessionStore((s) => s.sessions)
    const activeSessionId = useSessionStore((s) => s.activeSessionId)
    const daonMode = useSessionStore((s) => s.mode)

    // 유일한 Roo 타입 변환 지점(adapter/viewModel)을 통해 clineMessages 파생
    const clineMessages = useMemo(
        () =>
            toClineMessages(daonMessages, {
                streamStatus,
                lastUsage,
                hasPendingApproval: Object.keys(pendingApprovals).length > 0,
            }),
        [daonMessages, streamStatus, lastUsage, pendingApprovals],
    )

    // DAON 세션 목록 → Roo taskHistory
    const taskHistory = useMemo<HistoryItem[]>(
        () =>
            sessions.map((s, i) => ({
                id: s.session_id,
                number: i + 1,
                ts: (s.updated_at && Date.parse(s.updated_at)) || Date.now(),
                task: s.title || `세션 ${i + 1}`,
                tokensIn: 0,
                tokensOut: 0,
                totalCost: 0,
            })),
        [sessions],
    )

    // DAON 런타임 설치 (Normalizer + 세션/모델 부트스트랩) — 1회만
    useEffect(() => {
        installDaonRuntime()
    }, [])

    const setListApiConfigMeta = useCallback(
        (value: ProviderSettingsEntry[]) => setState((prevState) => ({ ...prevState, listApiConfigMeta: value })),
        [],
    )

    const setApiConfiguration = useCallback((value: ProviderSettings) => {
        setState((prevState) => ({
            ...prevState,
            apiConfiguration: {
                ...prevState.apiConfiguration,
                ...value,
            },
        }))
    }, [])

    const contextValue: ExtensionStateContextType = {
        ...state,
        // ── DAON 파생 값 (state 기본값 덮어씀) ──
        clineMessages,
        taskHistory,
        mode: daonMode,
        currentTaskId: activeSessionId ?? undefined,
        reasoningBlockCollapsed: state.reasoningBlockCollapsed ?? true,
        didHydrateState: true, // DAON은 확장 상태 hydrate가 필요 없음
        showWelcome: false, // 항상 채팅 화면
        theme,
        mcpServers,
        currentCheckpoint,
        filePaths,
        openedTabs,
        commands,
        soundVolume: state.soundVolume,
        ttsSpeed: state.ttsSpeed,
        writeDelayMs: state.writeDelayMs,
        profileThresholds: state.profileThresholds ?? {},
        alwaysAllowFollowupQuestions,
        followupAutoApproveTimeoutMs,
        setExperimentEnabled: (id, enabled) =>
            setState((prevState) => ({ ...prevState, experiments: { ...prevState.experiments, [id]: enabled } })),
        setApiConfiguration,
        setCustomInstructions: (value) => setState((prevState) => ({ ...prevState, customInstructions: value })),
        setAlwaysAllowReadOnly: (value) => setState((prevState) => ({ ...prevState, alwaysAllowReadOnly: value })),
        setAlwaysAllowReadOnlyOutsideWorkspace: (value) =>
            setState((prevState) => ({ ...prevState, alwaysAllowReadOnlyOutsideWorkspace: value })),
        setAlwaysAllowWrite: (value) => setState((prevState) => ({ ...prevState, alwaysAllowWrite: value })),
        setAlwaysAllowWriteOutsideWorkspace: (value) =>
            setState((prevState) => ({ ...prevState, alwaysAllowWriteOutsideWorkspace: value })),
        setAlwaysAllowExecute: (value) => setState((prevState) => ({ ...prevState, alwaysAllowExecute: value })),
        setAlwaysAllowMcp: (value) => setState((prevState) => ({ ...prevState, alwaysAllowMcp: value })),
        setAlwaysAllowModeSwitch: (value) => setState((prevState) => ({ ...prevState, alwaysAllowModeSwitch: value })),
        setAlwaysAllowSubtasks: (value) => setState((prevState) => ({ ...prevState, alwaysAllowSubtasks: value })),
        setAlwaysAllowFollowupQuestions,
        setFollowupAutoApproveTimeoutMs: (value) => {
            setFollowupAutoApproveTimeoutMsState(value)
            setState((prevState) => ({ ...prevState, followupAutoApproveTimeoutMs: value }))
        },
        setShowAnnouncement: (value) => setState((prevState) => ({ ...prevState, shouldShowAnnouncement: value })),
        setAllowedCommands: (value) => setState((prevState) => ({ ...prevState, allowedCommands: value })),
        setDeniedCommands: (value) => setState((prevState) => ({ ...prevState, deniedCommands: value })),
        setAllowedMaxRequests: (value) => setState((prevState) => ({ ...prevState, allowedMaxRequests: value })),
        setAllowedMaxCost: (value) => setState((prevState) => ({ ...prevState, allowedMaxCost: value })),
        setSoundEnabled: (value) => setState((prevState) => ({ ...prevState, soundEnabled: value })),
        setSoundVolume: (value) => setState((prevState) => ({ ...prevState, soundVolume: value })),
        setTtsEnabled: (value) => setState((prevState) => ({ ...prevState, ttsEnabled: value })),
        setTtsSpeed: (value) => setState((prevState) => ({ ...prevState, ttsSpeed: value })),
        setEnableCheckpoints: (value) => setState((prevState) => ({ ...prevState, enableCheckpoints: value })),
        setCheckpointTimeout: (value) => setState((prevState) => ({ ...prevState, checkpointTimeout: value })),
        setWriteDelayMs: (value) => setState((prevState) => ({ ...prevState, writeDelayMs: value })),
        setTerminalOutputPreviewSize: (value) =>
            setState((prevState) => ({ ...prevState, terminalOutputPreviewSize: value })),
        setTerminalShellIntegrationTimeout: (value) =>
            setState((prevState) => ({ ...prevState, terminalShellIntegrationTimeout: value })),
        setTerminalShellIntegrationDisabled: (value) =>
            setState((prevState) => ({ ...prevState, terminalShellIntegrationDisabled: value })),
        setTerminalZdotdir: (value) => setState((prevState) => ({ ...prevState, terminalZdotdir: value })),
        setMcpEnabled: (value) => setState((prevState) => ({ ...prevState, mcpEnabled: value })),
        setCurrentApiConfigName: (value) => setState((prevState) => ({ ...prevState, currentApiConfigName: value })),
        setListApiConfigMeta,
        // 모드 전환은 DAON sessionStore가 단일 진실 공급원
        setMode: (value: Mode) => useSessionStore.getState().setMode(value),
        setCustomModePrompts: (value) => setState((prevState) => ({ ...prevState, customModePrompts: value })),
        setCustomSupportPrompts: (value) => setState((prevState) => ({ ...prevState, customSupportPrompts: value })),
        setEnhancementApiConfigId: (value) =>
            setState((prevState) => ({ ...prevState, enhancementApiConfigId: value })),
        setAutoApprovalEnabled: (value) => setState((prevState) => ({ ...prevState, autoApprovalEnabled: value })),
        setCustomModes: (value) => setState((prevState) => ({ ...prevState, customModes: value })),
        setMaxOpenTabsContext: (value) => setState((prevState) => ({ ...prevState, maxOpenTabsContext: value })),
        setMaxWorkspaceFiles: (value) => setState((prevState) => ({ ...prevState, maxWorkspaceFiles: value })),
        setShowRooIgnoredFiles: (value) => setState((prevState) => ({ ...prevState, showRooIgnoredFiles: value })),
        setEnableSubfolderRules: (value) => setState((prevState) => ({ ...prevState, enableSubfolderRules: value })),
        setAwsUsePromptCache: (value) => setState((prevState) => ({ ...prevState, awsUsePromptCache: value })),
        setMaxImageFileSize: (value) => setState((prevState) => ({ ...prevState, maxImageFileSize: value })),
        setMaxTotalImageSize: (value) => setState((prevState) => ({ ...prevState, maxTotalImageSize: value })),
        setPinnedApiConfigs: (value) => setState((prevState) => ({ ...prevState, pinnedApiConfigs: value })),
        togglePinnedApiConfig: (configId) =>
            setState((prevState) => {
                const currentPinned = prevState.pinnedApiConfigs || {}
                const newPinned = {
                    ...currentPinned,
                    [configId]: !currentPinned[configId],
                }

                // If the config is now unpinned, remove it from the object
                if (!newPinned[configId]) {
                    delete newPinned[configId]
                }

                return { ...prevState, pinnedApiConfigs: newPinned }
            }),
        setHistoryPreviewCollapsed: (value) =>
            setState((prevState) => ({ ...prevState, historyPreviewCollapsed: value })),
        setReasoningBlockCollapsed: (value) =>
            setState((prevState) => ({ ...prevState, reasoningBlockCollapsed: value })),
        enterBehavior: state.enterBehavior ?? "send",
        setEnterBehavior: (value) => setState((prevState) => ({ ...prevState, enterBehavior: value })),
        setHasOpenedModeSelector: (value) => setState((prevState) => ({ ...prevState, hasOpenedModeSelector: value })),
        setAutoCondenseContext: (value) => setState((prevState) => ({ ...prevState, autoCondenseContext: value })),
        setAutoCondenseContextPercent: (value) =>
            setState((prevState) => ({ ...prevState, autoCondenseContextPercent: value })),
        setProfileThresholds: (value) => setState((prevState) => ({ ...prevState, profileThresholds: value })),
        includeDiagnosticMessages: state.includeDiagnosticMessages,
        setIncludeDiagnosticMessages: (value) => {
            setState((prevState) => ({ ...prevState, includeDiagnosticMessages: value }))
        },
        maxDiagnosticMessages: state.maxDiagnosticMessages,
        setMaxDiagnosticMessages: (value) => {
            setState((prevState) => ({ ...prevState, maxDiagnosticMessages: value }))
        },
        includeTaskHistoryInEnhance,
        setIncludeTaskHistoryInEnhance,
        includeCurrentTime,
        setIncludeCurrentTime,
        includeCurrentCost,
        setIncludeCurrentCost,
        skills,
        showWorktreesInHomeScreen: state.showWorktreesInHomeScreen ?? true,
        setShowWorktreesInHomeScreen: (value) =>
            setState((prevState) => ({ ...prevState, showWorktreesInHomeScreen: value })),
    }

    return <ExtensionStateContext.Provider value={contextValue}>{children}</ExtensionStateContext.Provider>
}

export const useExtensionState = () => {
    const context = useContext(ExtensionStateContext)

    if (context === undefined) {
        throw new Error("useExtensionState must be used within an ExtensionStateContextProvider")
    }

    return context
}
