/**
 * [2026-08-31 승인 요청 음성 안내] static/modules/chat.js 패치
 *
 * 요청: 도구 사용은 음성 안내가 나오는데 승인 요청은 나오지 않는다.
 *   사용자가 딴짓하다가도 승인 요청이 오면 화면을 봐야 하므로 음성 안내 필요.
 *
 * 수정: approval SSE 이벤트 처리에서
 *   - pending(승인 대기) 수신 시: "승인이 필요합니다. 화면에서 확인해 주세요."
 *     (위험 명령은 "위험 명령 승인이 필요합니다...")
 *   - auto_approved(45초 무응답 자동 승인) 수신 시: "응답이 없어 자동 승인했습니다."
 *   음성으로 안내한다. speak()는 static/modules/speak.js의 전역 함수(SpeechSynthesis).
 */
const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, '..', 'static', 'modules', 'chat.js');
let src = fs.readFileSync(FILE, 'utf8');

const beforeMixed = (src.match(/\r\r+\n/g) || []).length;
if (beforeMixed > 0) {
    src = src.replace(/\r\n/g, '\n').replace(/\r+/g, '').replace(/\n/g, '\r\n');
    console.log(`줄바꿈 정규화: ${beforeMixed}개 정리`);
}
const toCRLF = (s) => s.replace(/\r?\n/g, '\r\n');

const patches = [
    {
        name: '1) 승인 대기(pending) 수신 시 음성 안내',
        find: toCRLF([
            "          if (data.type === 'dangerous_command') {",
            "            setStreamStatus('thinking', '⚠️ 위험 명령 승인 대기 중...');",
            '          } else {',
            "            setStreamStatus('thinking', '🛡️ 승인 대기 중...');",
            '          }',
        ].join('\n')),
        replace: toCRLF([
            "          if (data.type === 'dangerous_command') {",
            "            setStreamStatus('thinking', '⚠️ 위험 명령 승인 대기 중...');",
            '          } else {',
            "            setStreamStatus('thinking', '🛡️ 승인 대기 중...');",
            '          }',
            '          // [2026-08-31] 승인 요청 음성 안내 — 사용자가 딴짓하다가도 화면을',
            '          // 보게 한다. 도구 사용 음성 안내와 동일한 speak() 경로를 사용한다.',
            '          try {',
            "            if (typeof speak === 'function') {",
            "              speak(data.type === 'dangerous_command'",
            "                ? '위험 명령 승인이 필요합니다. 화면에서 확인해 주세요.'",
            "                : '승인이 필요합니다. 화면에서 확인해 주세요.');",
            '            }',
            '          } catch (_) { }',
        ].join('\n')),
    },
    {
        name: '2) 자동 승인(auto_approved) 수신 시 음성 안내',
        find: toCRLF([
            "          if (data.type === 'dangerous_command') {",
            '          // [2026-08-27] restore the web view hidden during approval wait',
            "            setStreamStatus('thinking', '⏱️ 응답 없음 — 자동 승인됨');",
            '          } else {',
            "            setStreamStatus('thinking', '⏱️ 자동 승인됨');",
            '          }',
        ].join('\n')),
        replace: toCRLF([
            "          if (data.type === 'dangerous_command') {",
            '          // [2026-08-27] restore the web view hidden during approval wait',
            "            setStreamStatus('thinking', '⏱️ 응답 없음 — 자동 승인됨');",
            '          } else {',
            "            setStreamStatus('thinking', '⏱️ 자동 승인됨');",
            '          }',
            '          // [2026-08-31] 자동 승인 음성 안내 — 승인 카드가 사라진 이유를 알린다.',
            '          try {',
            "            if (typeof speak === 'function') {",
            "              speak('응답이 없어 자동 승인했습니다.');",
            '            }',
            '          } catch (_) { }',
        ].join('\n')),
    },
];

let applied = 0;
for (const p of patches) {
    const findCRLF = toCRLF(p.find);
    const replaceCRLF = toCRLF(p.replace);
    const count = src.split(findCRLF).length - 1;
    if (count !== 1) {
        console.error(`[FAIL] ${p.name} — 매칭 ${count}회 (1회여야 함)`);
        process.exit(1);
    }
    src = src.replace(findCRLF, replaceCRLF);
    applied++;
    console.log(`[OK] ${p.name}`);
}

fs.writeFileSync(FILE, src, 'utf8');
console.log(`\n완료: ${applied}/${patches.length} 패치 적용 → ${FILE}`);

try {
    new Function(src);
    console.log('문법 체크: OK');
} catch (e) {
    console.error('문법 체크 실패:', e.message);
    process.exit(1);
}
