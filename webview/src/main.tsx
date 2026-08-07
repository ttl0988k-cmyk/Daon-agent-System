/**
 * DAON webview 엔트리.
 *
 * Roo 원본 index.tsx(src/roo/index.tsx)와 동일한 부트스트랩 순서를 따르되,
 * App은 DAON 전용 엔트리(./App)를 사용한다. Roo 원본 App은 상대 경로 import로
 * shim alias를 우회하기 때문이다.
 */
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./roo/index.css"
import "../node_modules/@vscode/codicons/dist/codicon.css"

import App from "./App"
import { getHighlighter } from "./roo/utils/highlighter"

// Initialize Shiki early to hide initialization latency (async)
getHighlighter().catch((error: Error) => console.error("Failed to initialize Shiki highlighter:", error))

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
