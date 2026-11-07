#!/usr/bin/env bash
# 복원본을 로컬 서빙하고 다온 내부 브라우저(8088)로 띄운다.
#
# 사용:
#   serve_and_open.sh <rebuilt_dir> <entry_relpath> [port]
# 예:
#   serve_and_open.sh /c/daon/_rea_rebuilt examples/webgl_shaders_ocean.html 18765
#
# ⚠️ 금지 포트: 8099(auth-gate) / 8095(라온스튜디오) / 9090(다온 본체) / 8088(브라우저 서비스)
set -euo pipefail

REBUILT="${1:?rebuilt_dir 필요}"
ENTRY="${2:?entry_relpath 필요}"
PORT="${3:-18765}"

if netstat -ano | grep -q ":$PORT "; then
  echo "❌ 포트 $PORT 는 이미 사용 중입니다 — 다른 포트를 지정하세요" >&2
  exit 1
fi

( cd "$REBUILT" && nohup python -m http.server "$PORT" --bind 127.0.0.1 \
    >/dev/null 2>&1 & )
sleep 2

URL="http://127.0.0.1:$PORT/$ENTRY"
echo "▶ 서빙: $URL"
curl -s -o /dev/null -w "  HTTP %{http_code}\n" "$URL"

echo "▶ 다온 내부 브라우저로 띄우기…"
curl -s -X POST http://127.0.0.1:9090/api/browser/navigate \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"$URL\"}" | head -c 300
echo

sleep 3
echo "▶ 8088 뷰어 상태 (viewers_count 1 이면 챗창 카드 붙음):"
curl -s http://127.0.0.1:8088/health
echo
