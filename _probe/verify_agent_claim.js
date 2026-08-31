const fs = require('fs');
const lines = fs.readFileSync('static/modules/chat.js', 'utf8').split('\n');

console.log('=== 1) [2026-08-31b] 전량 삭제 라인 (에이전트가 지적한 곳) ===');
lines.forEach((l, i) => {
    if (l.includes('2026-08-31b') || l.includes("querySelectorAll('.tool-group-card')")) {
        console.log((i + 1) + ': ' + l.trim().substring(0, 110));
    }
});

console.log('\n=== 2) 911줄 근처 원래 설계 주석 (Roo 스타일 순차 누적) ===');
for (let i = 903; i <= 912; i++) console.log((i + 1) + ': ' + lines[i].trim().substring(0, 110));

console.log('\n=== 3) token 핸들러의 카드 제거 철회 상태 ===');
lines.forEach((l, i) => {
    if (l.includes('카드는 여기서 지우지 않는다')) console.log((i + 1) + ': ' + l.trim().substring(0, 110));
});
