---
name: notification-relay
version: "1.0"
category: integration
priority: medium
tags:
  - slack
  - notion
  - notification
  - delivery
  - webhook
  - integration
conflicts_with: []
graph_requires: []
graph_compatible:
  - sherlock-qa
  - taste
  - landing-page
graph_conflicts: []
purpose: "에이전트 결과물을 Slack/Notion으로 자동 전파하는 알림 연동을 제공한다"
when_to_use: "분석 보고서, 코드 리뷰, 작업 완료 알림을 Slack/Notion으로 전송할 때"
when_not_to_use: "실시간 양방향 대화, 코드 생성, UI 디자인"
inputs: "전송할 결과물(텍스트/마크다운), 대상 플랫폼(slack/notion), 채널/DB 지정"
outputs: "Slack 메시지, Notion 페이지"
examples: "코드 분석 결과 Slack 전송, 작업 완료 보고서 Notion 기록"
constraints: "Slack 4000자 초과 시 분할 전송, 민감 정보(API 키 등) 전송 금지, 자동 전송 금지"
success_criteria: "플랫폼별 포맷팅 규칙 준수, 민감 정보 필터링 완료, 전송 실패 시 대안 제시"
---

# Notification Relay — Slack & Notion 연동

## 개요

에이전트가 생성한 결과물(분석 보고서, 코드 리뷰, 문서, 작업 완료 알림 등)을
Slack 채널이나 Notion 데이터베이스로 자동 전송하는 방법을 설명한다.

## Slack 연동

### 메시지 전송 규칙

1. **형식**: Markdown (Slack mrkdwn) 형식으로 변환하여 전달
2. **길이 제한**: 4000자 이상일 경우 여러 메시지로 분할
3. **코드 블록**: ```언어 ... ``` 형태 유지
4. **헤더 규칙**: `*제목*` 또는 `*이슈 #123*` 형태로 제목 강조

### 전송 대상 판단

- 사용자가 "Slack으로 보내줘" 라고 하면 → Slack 전송
- 채널을 지정하면 해당 채널로, 없으면 기본 채널로
- `send_message` 도구 사용 시 target 예시:
  - `slack` (기본 채널)
  - `slack:#general`
  - `slack:#bot-test`

### Slack 메시지 포맷 예시

```
*📊 분석 완료: Daon Agent System 코드 리뷰*
- 총 파일: 89개
- 이슈 발견: 3건 (심각 1, 경미 2)
- 평균 품질 점수: 87/100

*주요 발견 사항:*
1. 🔴 `api/streaming.py`: 메모리 누수 가능성 - 452라인
2. 🟡 `static/modules/chat.js`: 비동기 에러 핸들링 부재 - 203라인
3. 🟡 `config.yaml`: 누락된 환경변수 문서화

전체 보고서: ↓
```

## Notion 연동

### 페이지 생성 규칙

1. **제목**: `[날짜] 작업 유형 - 요약` 형식
   - 예: `[2026-06-28] 코드 분석 - Daon Agent System`
2. **속성**:
   - Title: 페이지 제목
   - Tags: Multi-select (슬래시로 구분)
   - Status: 완료 상태
3. **본문**: Markdown을 Notion 블록으로 변환
   - 헤딩 → `heading_1`, `heading_2`, `heading_3`
   - 코드 블록 → `code` 블록 (language 지정)
   - 글머리 → `bulleted_list_item`
   - 번호 → `numbered_list_item`
   - 구분선 → `divider`

### 전송 대상 판단

- 사용자가 "Notion에 기록해줘" 라고 하면 → Notion 전송
- `send_message` 도구 사용 시 target 예시:
  - `notion` (기본 데이터베이스)
  - `notion:database_id` (특정 DB)

### Notion 페이지 템플릿

```markdown
# 📋 작업 결과: [작업명]

## 📊 요약
- 작업 유형: 코드 분석
- 실행 시간: 2.3초
- 상태: 완료

## 📝 상세 결과
[분석 내용...]

## 🔗 참조
- 세션 ID: abc123
- 작업공간: /path/to/project
```

## 환경 변수 설정 가이드

Slack과 Notion 연동을 위해 다음 환경 변수가 필요하다:

```bash
# Slack (둘 중 하나만 있으면 됨)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxxx
SLACK_BOT_TOKEN=xoxb-...           # Bot User OAuth Token
SLACK_DEFAULT_CHANNEL=#general      # 기본 대상 채널

# Notion
NOTION_TOKEN=secret_...             # Internal Integration Token
NOTION_DATABASE_ID=abc123...        # 기본 대상 데이터베이스 ID
```

## 에이전트 행동 지침

1. **자동 판단 금지**: 사용자가 명시적으로 Slack/Notion 전송을 요청할 때만 사용
2. **결과 포맷팅**: 원본 출력을 그대로 보내지 말고, 플랫폼에 맞게 포맷팅
3. **오류 처리**: 전송 실패 시 사용자에게 실패 원인과 대안을 제시
4. **민감 정보**: API 키, 비밀번호 등 민감 정보는 전송 내용에서 제외
5. **파일 크기**: 10KB 이상의 결과는 요약본만 보내고 전체 결과는 파일로 첨부

## send_message 통합

이미 빌트인 `send_message` 도구가 Slack과 Notion을 지원한다.
이 스킬은 send_message 사용 시의 포맷팅 및 컨벤션을 정의한다.

사용 가능한 target 형식:
- `slack` → 기본 Slack 채널
- `slack:#채널명` → 특정 Slack 채널
- `notion` → 기본 Notion 데이터베이스
