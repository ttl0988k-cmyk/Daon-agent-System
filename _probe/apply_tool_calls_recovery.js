/**
 * [2026-08-31 도구 카드 소실 근본 해결] chat.js + streaming.py 패치
 *
 * 구조적 원인 (실측 확정):
 *  - 세션 메시지는 OpenAI 형식: assistant.tool_calls = [{id, function:{name, arguments}}]
 *    (실측: assistant 69개 중 57개가 tool_calls 보유, role=tool 61개)
 *  - 그런데 streaming.py의 tool_calls 수집 로직은 Anthropic 형식
 *    (content가 list이고 tool_use 블록)만 파싱 → 세션 저장 시 tool_calls가
 *    항상 0개 → done 후 renderMessages가 도구 카드를 재생성하지 못함
 *    → 도구 전용 턴은 빈 블록(얇은 라인)만 남음 = "도구 카드가 사라지고
 *      라인만 보인다"의 진짜 원인.
 *
 * 해결:
 *  1) chat.js renderMessages: toolCalls가 비어있으면 messages에서 OpenAI 형식을
 *     직접 파싱해 도구 카드를 복원 (재빌드 불필요, 즉시 효과)
 *  2) chat.js _onToolEvent: _thinking 내부 마커를 카드에 노출하지 않음 +
 *     completed의 tool_call_id 불일치 시 같은 이름 '실행 중' 항목에 완료 처리
 *     (중복 항목 방지 — 에이전트가 발견한 버그)
 *  3) streaming.py: OpenAI 형식 tool_calls 파싱 추가 (다음 재빌드 시 반영)
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');

function patchFile(rel, patches, eolForce) {
    const FILE = path.join(ROOT, rel);
    let src = fs.readFileSync(FILE, 'utf8');
    const beforeMixed = (src.match(/\r\r+\n/g) || []).length;
    if (beforeMixed > 0) {
        src = src.replace(/\r\n/g, '\n').replace(/\r+/g, '').replace(/\n/g, '\r\n');
        console.log(`[${rel}] 줄바꿈 정규화: ${beforeMixed}개`);
    }
    const usesCRLF = eolForce === 'crlf' || (src.match(/\r\n/g) || []).length > 0;
    const toEOL = (s) => usesCRLF ? s.replace(/\r?\n/g, '\r\n') : s.replace(/\r?\n/g, '\n');
    console.log(`[${rel}] 줄바꿈: ${usesCRLF ? 'CRLF' : 'LF'}`);

    let applied = 0;
    for (const p of patches) {
        const f = toEOL(p.find), r = toEOL(p.replace);
        const count = src.split(f).length - 1;
        if (count !== 1) {
            console.error(`[FAIL] ${rel} :: ${p.name} — 매칭 ${count}회`);
            process.exit(1);
        }
        src = src.replace(f, r);
        applied++;
        console.log(`[OK] ${rel} :: ${p.name}`);
    }
    fs.writeFileSync(FILE, src, 'utf8');
    try {
        if (rel.endsWith('.js')) new Function(src);
        console.log(`[${rel}] 문법 체크: OK`);
    } catch (e) {
        console.error(`[${rel}] 문법 체크 실패:`, e.message);
        process.exit(1);
    }
}

// ═══════════ chat.js ═══════════
patchFile('static/modules/chat.js', [
    {
        name: '1) renderMessages: toolCalls 비었으면 messages에서 OpenAI 형식 직접 파싱',
        find: [
            'function renderMessages(messages, toolCalls) {',
            "  const box = $('chatMessages');",
        ].join('\n'),
        replace: [
            'function renderMessages(messages, toolCalls) {',
            "  const box = $('chatMessages');",
            '  // ── [2026-08-31 도구 카드 소실 근본 해결] ──',
            '  // 서버(streaming.py)의 tool_calls 수집이 Anthropic 형식(content 내 tool_use)',
            '  // 만 파싱하기 때문에 OpenAI 호환 모델(Qwen 등)에서는 세션 저장 시 tool_calls가',
            '  // 항상 0개다. 그러면 done 후 히스토리 재렌더링 시 도구 카드가 재생성되지 않고,',
            '  // 도구 전용 턴은 빈 블록(얇은 라인)만 남았다. 여기서 messages의 OpenAI 형식',
            '  // (assistant.tool_calls + role=tool 결과)을 직접 파싱해 복원한다.',
            '  if ((!toolCalls || toolCalls.length === 0) && Array.isArray(messages)) {',
            '    toolCalls = (function _parseToolCallsFromMessages(msgs) {',
            '      var out = [];',
            '      var pending = {};  // tool_call_id -> {name, args, asstIdx}',
            '      msgs.forEach(function (m, idx) {',
            "        if (!m) return;",
            "        if (m.role === 'assistant' && Array.isArray(m.tool_calls)) {",
            '          m.tool_calls.forEach(function (tc) {',
            '            if (!tc || typeof tc !== \'object\') return;',
            "            var tid = tc.id || tc.call_id || '';",
            "            var fname = (tc.function && tc.function.name) || tc.name || '';",
            '            if (!tid || !fname) return;',
            '            var args = {};',
            '            try { args = JSON.parse((tc.function && tc.function.arguments) || \'{}\'); } catch (_) { }',
            '            pending[tid] = { name: fname, args: args, asstIdx: idx };',
            '          });',
            "        } else if (m.role === 'tool') {",
            "          var tid2 = m.tool_call_id || m.tool_use_id || '';",
            '          var p = pending[tid2];',
            '          if (p) {',
            '            out.push({',
            '              name: p.name,',
            "              snippet: String(m.content || '').substring(0, 200),",
            '              tid: tid2,',
            '              assistant_msg_idx: p.asstIdx,',
            '              args: p.args,',
            '            });',
            '          }',
            '        }',
            '      });',
            '      return out;',
            '    })(messages);',
            '    if (toolCalls.length) console.log(\'[renderMessages] tool_calls 복원(OpenAI 형식 파싱):\', toolCalls.length, \'개\');',
            '  }',
        ].join('\n'),
    },
    {
        name: '2) _onToolEvent: _thinking 내부 마커는 카드에 노출하지 않음',
        find: [
            '      // ── 도구 그룹 카드: 반복 호출을 하나의 접이식 카드로 묶음 ──',
            '      // 그룹 카드가 없으면 새로 생성 (reasoning 카드와 같이 box에 독립 삽입)',
            '      if (!_toolGroupCard) {',
        ].join('\n'),
        replace: [
            '      // [2026-08-31] _thinking은 내부 추론 마커다 — 도구 카드에 노출하지 않는다',
            '      // (카운트는 started/completed 쌍을 맞춰야 하므로 유지하고 항목만 숨긴다)',
            '      var _isInternalMarker = (toolName === \'_thinking\');',
            '',
            '      // ── 도구 그룹 카드: 반복 호출을 하나의 접이식 카드로 묶음 ──',
            '      // 그룹 카드가 없으면 새로 생성 (reasoning 카드와 같이 box에 독립 삽입)',
            '      if (!_toolGroupCard && !_isInternalMarker) {',
        ].join('\n'),
    },
    {
        name: '3) started 항목 추가 시 내부 마커 스킵',
        find: [
            '      if (isStarted) {',
            '        _toolGroupCount++;',
            '        // 새 항목 추가',
            '        const item = document.createElement(\'div\');',
            "        item.className = 'tool-group-item';",
            '        item.innerHTML = `',
            '          <span class="tgi-icon">⏳</span>',
            '          <span class="tgi-name">${toolName}</span>',
            '          <span class="tgi-status">실행 중</span>',
            '        `;',
            '        _toolItemMap[toolCallId] = item;',
            '        if (_toolGroupItems) _toolGroupItems.appendChild(item);',
            '      } else {',
        ].join('\n'),
        replace: [
            '      if (isStarted) {',
            '        _toolGroupCount++;',
            '        // 새 항목 추가 (내부 마커는 카운트만 유지하고 항목은 숨김)',
            '        if (!_isInternalMarker) {',
            '        const item = document.createElement(\'div\');',
            "        item.className = 'tool-group-item';",
            '        item.innerHTML = `',
            '          <span class="tgi-icon">⏳</span>',
            '          <span class="tgi-name">${toolName}</span>',
            '          <span class="tgi-status">실행 중</span>',
            '        `;',
            '        _toolItemMap[toolCallId] = item;',
            '        if (_toolGroupItems) _toolGroupItems.appendChild(item);',
            '        }',
            '      } else {',
        ].join('\n'),
    },
    {
        name: '4) completed: tool_call_id 불일치 시 같은 이름 실행 중 항목에 완료 처리 (중복 방지)',
        find: [
            '        // completed: 기존 항목을 찾아 상태 업데이트',
            '        _toolGroupDoneCount++;',
            '        const existingItem = _toolItemMap[toolCallId];',
            '        if (existingItem) {',
        ].join('\n'),
        replace: [
            '        // completed: 기존 항목을 찾아 상태 업데이트',
            '        _toolGroupDoneCount++;',
            '        let existingItem = _toolItemMap[toolCallId];',
            '        // [2026-08-31] tool_call_id 불일치 폴백 — 재연결/재전달로 id가 어긋나면',
            '        // "completed만 온 경우" 분기가 같은 도구를 중복 추가했다. 같은 이름의',
            '        // 실행 중 항목을 찾아 그 항목을 완료 처리한다.',
            '        if (!existingItem && !_isInternalMarker) {',
            '          for (var _tid in _toolItemMap) {',
            '            var _it = _toolItemMap[_tid];',
            "            var _nm = _it.querySelector('.tgi-name');",
            "            var _st = _it.querySelector('.tgi-status');",
            "            if (_nm && _nm.textContent === toolName && _st && _st.textContent === '실행 중') {",
            '              existingItem = _it;',
            '              break;',
            '            }',
            '          }',
            '        }',
            '        if (existingItem) {',
        ].join('\n'),
    },
    {
        name: '5) completed 항목 갱신도 내부 마커 스킵',
        find: [
            '        } else {',
            '          // started 없이 completed만 온 경우 — 항목 새로 추가',
            '          _toolGroupCount++;',
            '          const item = document.createElement(\'div\');',
            "          item.className = 'tool-group-item';",
            '          item.innerHTML = `',
            '            <span class="tgi-icon">✅</span>',
            '            <span class="tgi-name">${toolName}</span>',
            '            <span class="tgi-status">완료</span>',
            '          `;',
            '          if (_toolGroupItems) _toolGroupItems.appendChild(item);',
            '        }',
        ].join('\n'),
        replace: [
            '        } else if (!_isInternalMarker) {',
            '          // started 없이 completed만 온 경우 — 항목 새로 추가',
            '          _toolGroupCount++;',
            '          const item = document.createElement(\'div\');',
            "          item.className = 'tool-group-item';",
            '          item.innerHTML = `',
            '            <span class="tgi-icon">✅</span>',
            '            <span class="tgi-name">${toolName}</span>',
            '            <span class="tgi-status">완료</span>',
            '          `;',
            '          if (_toolGroupItems) _toolGroupItems.appendChild(item);',
            '        }',
        ].join('\n'),
    },
], 'crlf');

// ═══════════ streaming.py (서버 — 다음 재빌드 시 반영) ═══════════
patchFile('api/api/streaming.py', [
    {
        name: '6) tool_calls 수집에 OpenAI 형식(assistant.tool_calls 필드) 파싱 추가',
        find: [
            "          for msg_idx, m in enumerate(s.messages):",
            "              if m.get('role') == 'assistant':",
            "                  c = m.get('content', '')",
            "                  if isinstance(c, list):",
        ].join('\n'),
        replace: [
            "          for msg_idx, m in enumerate(s.messages):",
            "              if m.get('role') == 'assistant':",
            "                  # [2026-08-31] OpenAI 호환 형식(assistant.tool_calls 필드) 파싱 —",
            "                  # Qwen 등 OpenAI 호환 모델은 tool 호출이 content가 아니라",
            "                  # m['tool_calls'] 필드([{id, function:{name, arguments}}])로 온다.",
            "                  # 기존 로직은 Anthropic 형식(content 내 tool_use 블록)만 파싱해",
            "                  # 이 모델들에서 tool_calls가 항상 0개였다(도구 카드 소실 원인).",
            "                  for _tc in (m.get('tool_calls') or []):",
            "                      if not isinstance(_tc, dict):",
            "                          continue",
            "                      _fn = _tc.get('function') or {}",
            "                      _tid = _tc.get('id') or _tc.get('call_id') or ''",
            "                      _tname = _fn.get('name') or _tc.get('name') or ''",
            "                      if _tid and _tname:",
            "                          pending_names[_tid] = _tname",
            "                          try:",
            "                              pending_args[_tid] = json.loads(_fn.get('arguments') or '{}')",
            "                          except Exception:",
            "                              pending_args[_tid] = {}",
            "                          pending_asst_idx[_tid] = msg_idx",
            "                  c = m.get('content', '')",
            "                  if isinstance(c, list):",
        ].join('\n'),
    },
], 'lf');

console.log('\n모든 패치 적용 완료');
