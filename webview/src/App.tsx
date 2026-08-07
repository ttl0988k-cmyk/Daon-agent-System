/**
 * DAON 채팅 엔트리 App.
 *
 * Roo 원본 App.tsx(src/roo/App.tsx)는 상대 경로("./context/...", "./utils/...")로
 * import 하기 때문에 Vite alias shim을 우회한다. 따라서 여기서 동일한 프로바이더
 * 스택을 직접 구성하고, Roo 원본 ChatView만 마운트한다.
 *
 * 프로바이더 순서는 Roo 원본 AppWithProviders와 동일:
 * ErrorBoundary > ExtensionStateContextProvider > TranslationProvider
 *   > QueryClientProvider > TooltipProvider
 *
 * - ExtensionStateContextProvider / TranslationProvider 는 DAON shim 경유
 *   (vite alias와 동일한 모듈로 수렴)
 * - ChatView 는 Roo 원본 바이트 그대로 사용 (수정 금지)
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import ErrorBoundary from "./roo/components/ErrorBoundary"
import { TooltipProvider } from "./roo/components/ui/tooltip"
import { STANDARD_TOOLTIP_DELAY } from "./roo/components/ui/standard-tooltip"
import ChatView from "./roo/components/chat/ChatView"
import { ExtensionStateContextProvider } from "./daon/shims/ExtensionStateContext"
import { TranslationProvider } from "./daon/shims/i18n"

const queryClient = new QueryClient()

export default function App() {
  return (
    <ErrorBoundary>
      <ExtensionStateContextProvider>
        <TranslationProvider>
          <QueryClientProvider client={queryClient}>
            <TooltipProvider delayDuration={STANDARD_TOOLTIP_DELAY}>
              <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
                <ChatView isHidden={false} showAnnouncement={false} hideAnnouncement={() => { }} />
              </div>
            </TooltipProvider>
          </QueryClientProvider>
        </TranslationProvider>
      </ExtensionStateContextProvider>
    </ErrorBoundary>
  )
}
