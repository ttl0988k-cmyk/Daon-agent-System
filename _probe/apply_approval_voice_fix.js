/**
 * [2026-08-31 승인 음성 안내 수정] static/modules/chat.js 패치
 *
 * 문제: 위험 명령 승인 요청 시 음성 안내가 나오지 않음.
 * 원인: 위험 명령 승인 이벤트는 status 필드가 없을 수 있다
 *   (approval.js 16줄 주석: "위험 명령 pending 데이터는 status 필드가 없을
 *    수 있어 type으로도 판별"). 그런데 추가한 음성 안내는
 *   data.status === 'pending' 블록 안에 있어서 status가 없으면 미실행.
 * 수정: pending 판별을 완화 — status === 'pending' 또는
 *   (status 없음 + type === 'dangerous_command') 모두 승인 대기로 처리.
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
        name: '1) approval 리스너: pending 판별 완화 (status 없는 위험 명령 포함)',
        find: toCRLF([
            "        if (data && data.status === 'pending') {",
            '          // 승인 대기 표시 — idle 워치독이 스트림을 종료하지 않게 유예한다.',
            '          // (백엔드는 사용자 응답을 기다리며 블로킹 중)',
            '          _approvalPending = true;',
            '          _idleExtensions = 0;',
        ].join('\n')),
        replace: toCRLF([
            '        // [2026-08-31] 위험 명령 승인은 status 필드가 없을 수 있다 (type으로 판별) —',
            '        // approval.js와 동일한 완화 판별을 적용해 음성 안내가 누락되지 않게 한다.',
            "        var _isApprovalPending = data && (data.status === 'pending' || (!data.status && data.type === 'dangerous_command'));",
            '        if (data && _isApprovalPending) {',
            '          // 승인 대기 표시 — idle 워치독이 스트림을 종료하지 않게 유예한다.',
            '          // (백엔드는 사용자 응답을 기다리며 블로킹 중)',
            '          _approvalPending = true;',
            '          _idleExtensions = 0;',
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
