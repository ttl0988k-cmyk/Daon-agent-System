const fs = require('fs');
const src = fs.readFileSync('static/modules/chat.js', 'utf8');
try {
    new Function(src);
    console.log('SYNTAX OK');
} catch (e) {
    console.log('SYNTAX ERROR:', e.message);
    // 에러 위치 추정: 스택에서 줄 번호 파싱
    const m = (e.stack || '').match(/<anonymous>:(\d+)/);
    if (m) {
        const line = parseInt(m[1], 10) - 2; // Function 래퍼 보정
        console.log('approx line:', line);
        for (let i = Math.max(0, line - 4); i < Math.min(src.split('\n').length, line + 3); i++) {
            console.log((i + 1) + ': ' + src.split('\n')[i]);
        }
    }
}
