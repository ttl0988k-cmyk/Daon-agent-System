/**
 * i18n shim — Roo의 `@src/i18n/TranslationContext` 자리에 alias되는 파일.
 *
 * Roo 원본 TranslationContext를 그대로 위임 사용한다.
 * (i18next + react-i18next + vendored locale JSON 기반, 언어는
 * ExtensionStateContext shim의 `language`(기본 "ko")를 따른다.)
 *
 * 나중에 번역 엔진을 바꾸거나 키 패스스루로 대체하려면 이 파일만 수정하면 된다.
 * Roo 컴포넌트가 직접 `react-i18next`의 Trans/useTranslation을 import하므로
 * i18next/react-i18next 패키지는 항상 설치되어 있어야 한다.
 */

export * from "../../roo/i18n/TranslationContext"
export { default } from "../../roo/i18n/TranslationContext"
