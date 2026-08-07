import { resolve } from "path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

/**
 * DAON webview 빌드 설정.
 *
 * Roo 원본 파일은 수정하지 않고, Vite alias로만 DAON shim을 주입한다.
 * - @src/utils/vscode → daon/shims/vscode.ts
 * - @src/context/ExtensionStateContext → daon/shims/ExtensionStateContext.tsx
 * - @src/i18n/TranslationContext → daon/shims/i18n.tsx
 *
 * 나머지 @src/*, @roo/*, @roo-code/types는 vendor된 Roo 원본으로 해석.
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: [
      // ── DAON shim (Roo 원본 대체, 최우선 매칭) ──
      // Roo 파일들은 `@src/...` 와 `@/...` 두 가지 import 형태를 혼용하므로
      // 두 형태 모두 shim으로 매핑해야 원본 모듈로 새지 않는다.
      { find: "@src/utils/vscode", replacement: resolve(__dirname, "src/daon/shims/vscode.ts") },
      { find: "@/utils/vscode", replacement: resolve(__dirname, "src/daon/shims/vscode.ts") },
      {
        find: "@src/context/ExtensionStateContext",
        replacement: resolve(__dirname, "src/daon/shims/ExtensionStateContext.tsx"),
      },
      {
        find: "@/context/ExtensionStateContext",
        replacement: resolve(__dirname, "src/daon/shims/ExtensionStateContext.tsx"),
      },
      { find: "@src/i18n/TranslationContext", replacement: resolve(__dirname, "src/daon/shims/i18n.tsx") },
      { find: "@/i18n/TranslationContext", replacement: resolve(__dirname, "src/daon/shims/i18n.tsx") },

      // ── DAON 스텁 (Roo 모노레포 워크스페이스 패키지 / 확장 호스트 모듈 대체) ──
      { find: "@roo-code/core/browser", replacement: resolve(__dirname, "src/daon/roo-core/browser.ts") },
      { find: /^vscode$/, replacement: resolve(__dirname, "src/daon/stubs/vscode.ts") },
      { find: "@anthropic-ai/sdk", replacement: resolve(__dirname, "src/daon/stubs/anthropic-sdk.ts") },
      { find: "ai-sdk-provider-poe/code", replacement: resolve(__dirname, "src/daon/stubs/poe-provider.ts") },

      // ── Roo 원본 경로 ──
      // 주의: "@roo-code/types"는 "@roo"보다 먼저 매칭되어야 한다.
      { find: "@roo-code/types", replacement: resolve(__dirname, "src/roo-types/index.ts") },
      { find: "@src", replacement: resolve(__dirname, "src/roo") },
      { find: "@roo", replacement: resolve(__dirname, "src/roo-shared") },
      { find: "@", replacement: resolve(__dirname, "src/roo") },
    ],
  },
  define: {
    // roo-shared/package.ts가 process.env.PKG_* 를 참조한다.
    // 브라우저에서는 process가 없으므로 define 치환이 필수다 (4개 모두).
    "process.env.PKG_NAME": JSON.stringify("daon"),
    "process.env.PKG_VERSION": JSON.stringify("1.0.0"),
    "process.env.PKG_OUTPUT_CHANNEL": JSON.stringify("DAON"),
    "process.env.PKG_SHA": JSON.stringify(""),
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5199,
    proxy: {
      // 개발 시 DAON 백엔드로 프록시
      "/api": {
        target: "http://localhost:8777",
        changeOrigin: true,
      },
    },
  },
})
