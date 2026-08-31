const fs = require('fs');
const lines = fs.readFileSync('static/modules/chat.js', 'utf8').split('\n');

console.log('=== 1) _onReasoningEvent의 새 카드 생성부 ===');
lines.forEach((l, i) => {
    if (l.includes('_onReasoningEvent = function')) {
        for (let k = i; k < Math.min(i + 14, lines.length); k++) {
            console.log((k + 1) + ': ' + lines[k]);
        }
    }
});

console.log('\n=== 2) renderMessages의 도구 카드 재렌더 블록 ===');
lines.forEach((l, i) => {
    if (l.includes('Find tool calls matching this assistant message')) {
        for (let k = i - 2; k < Math.min(i + 45, lines.length); k++) {
            console.log((k + 1) + ': ' + lines[k]);
        }
    }
});

console.log('\n=== 3) 도구 전용 턴 판정부 ===');
lines.forEach((l, i) => {
    if (l.includes('msgToolsPre')) {
        for (let k = i - 6; k <= i + 2; k++) console.log((k + 1) + ': ' + lines[k]);
        console.log('---');
    }
});
