// ── 브랜드 아이콘 매핑 (GPT 스토어 스타일) ─────────────────────────────
var _STORE_ICON_RULES = [
  [/docx|word/, '\u{1F4C4}'],
  [/xlsx|excel|spreadsheet/, '\u{1F4CA}'],
  [/\bpdf\b|form fields/, '\u{1F4D1}'],
  [/pptx|slide|deck|presentation/, '\u{1F4FD}'],
  [/seo/, '\u{1F50D}'],
  [/python|\buv-/, '\u{1F40D}'],
  [/javascript|typescript|nodejs/, '\u{1F7E1}'],
  [/design|canvas|art\b|theme|brand|landing/, '\u{1F3A8}'],
  [/security|sast|threat|stride|pci/, '\u{1F6E1}\uFE0F'],
  [/audit|signed|ship-mate|protect/, '\u{1F4DC}'],
  [/database|postgres|sql|data-engineering/, '\u{1F5C4}\uFE0F'],
  [/\bmcp\b|api|backend|scaffold/, '\u{1F50C}'],
  [/test|debug|tdd|review|quality/, '\u{1F9EA}'],
  [/cloud|deploy|kubernetes|cicd|infra/, '\u2601\uFE0F'],
  [/content|marketing|social|comms/, '\u{1F4E3}'],
  [/llm|prompt|rag|agent|machine-learning|skill-creator/, '\u{1F916}'],
  [/game/, '\u{1F3AE}'],
  [/payment|stripe|paypal|billing|trading|financ/, '\u{1F4B3}'],
  [/hr-|legal|business|startup|customer|sales|analytics|operating/, '\u{1F4BC}'],
  [/shell|bash|script/, '\u{1F47E}'],
  [/accessibility|wcag/, '\u267F'],
  [/git\b|pr-workflow/, '\u{1F33F}'],
  [/incident|observab|monitor|error|resilien/, '\u{1F6A8}'],
  [/knowledge|coauthor|documentation|wiki/, '\u{1F4D8}'],
  [/frontend|webapp|web-artifacts|browser/, '\u{1F310}'],
];
var _STORE_CAT_ICONS = {
  '문서': '\u{1F4C4}', '디자인': '\u{1F3A8}', '개발': '\u{1F4BB}', '테스트/품질': '\u{1F9EA}',
  'LLM/AI': '\u{1F916}', '데이터': '\u{1F5C4}\uFE0F', '인프라/운영': '\u2699\uFE0F', '보안': '\u{1F6E1}\uFE0F',
  'SEO/콘텐츠': '\u{1F4E3}', '비즈니스': '\u{1F4BC}', '기타': '\u{1F4E6}',
};

function storeIconEmoji(it) {
  var hay = (it.name + ' ' + (it.desc || '')).toLowerCase();
  for (var i = 0; i < _STORE_ICON_RULES.length; i++) {
    if (_STORE_ICON_RULES[i][0].test(hay)) return _STORE_ICON_RULES[i][1];
  }
  return null;
}

function storeRenderCats() {
  var box = document.getElementById('pstoreCats');
  if (!box || !_storeState.catalog) return;
  var cats = ['전체', '\uD83D\uDD25 인기'].concat(_storeState.catalog.categories || []);
  var html = '';
  for (var i = 0; i < cats.length; i++) {
    var c = cats[i];
    var active = (_storeState.cat === c);
    var label = esc(c);
    if (_STORE_CAT_ICONS[c]) label = _STORE_CAT_ICONS[c] + ' ' + label;
    html += '<button class="pstore-cat' + (active ? ' active' : '') + '" onclick="storeSetCat(\'' + esc(c) + '\')">' + label + '</button>';
  }
  box.innerHTML = html;
}

function _storeMatches(it) {
  if (_storeState.cat !== '전체' && _storeState.cat !== '\uD83D\uDD25 인기' && it.category !== _storeState.cat) return false;
  if (_storeState.q) {
    var hay = (it.name + ' ' + (it.desc || '') + ' ' + it.category).toLowerCase();
    if (hay.indexOf(_storeState.q) === -1) return false;
  }
  return true;
}

function _storeInitials(name) {
  var parts = name.replace(/^anthropic-/, '').split(/[-_]/).filter(Boolean);
  if (parts.length >= 2) return (parts[0].charAt(0) + parts[1].charAt(0)).toUpperCase();
  return (parts[0] || 'P').slice(0, 2).toUpperCase();
}

function _storeRowHtml(it, inst, isHot) {
  var html = '';
  var emoji = storeIconEmoji(it);
  html += '<div class="pstore-row' + (inst ? ' installed' : '') + '">';
  if (emoji) {
    html += '  <div class="pstore-icon emoji">' + emoji + '</div>';
  } else {
    html += '  <div class="pstore-icon init" style="background:' + esc(it.color || '#6c8cff') + '">' + esc(_storeInitials(it.name)) + '</div>';
  }
  html += '  <div class="pstore-row-body">';
  html += '    <div class="pstore-row-name">' + esc(it.name.replace(/^anthropic-/, ''));
  if (it.src === 'anthropics') html += ' <span class="pstore-badge a">공식</span>';
  else html += ' <span class="pstore-badge w">W</span>';
  if (isHot) html += ' <span class="pstore-badge hot">\uD83D\uDD25</span>';
  html += ' <span class="pstore-skills">\uD83E\uDDE9 ' + (it.skills || 0) + '</span></div>';
  html += '    <div class="pstore-row-desc">' + esc((it.desc || '').slice(0, 120)) + ((it.desc || '').length > 120 ? '…' : '') + '</div>';
  html += '  </div>';
  if (inst) {
    html += '  <div class="pstore-row-act"><button class="pstore-btn on" onclick="storeSetView(\'installed\')" title="설치됨 — 관리 페이지로">✓ 설치됨</button></div>';
  } else {
    html += '  <div class="pstore-row-act"><button class="pstore-btn install" onclick="storeInstall(this,\'' + esc(it.name) + '\')">⬇ 설치</button></div>';
  }
  html += '</div>';
  return html;
}

function storeRenderGrid() {
  var grid = document.getElementById('pstoreGrid');
  var foot = document.getElementById('pstoreFooter');
  if (!grid) return;
  if (!_storeState.catalog) { return; }
  var installed = storeInstalledMap();
  var items = (_storeState.catalog.plugins || []).filter(_storeMatches);
  var html = '', shown = 0;

  if (_storeState.cat === '\uD83D\uDD25 인기') {
    items = items.slice().sort(function (a, b) { return (b.skills || 0) - (a.skills || 0); }).slice(0, 12);
    html += '<div class="pstore-section">\uD83D\uDD25 인기 플러그인</div>';
    for (var i = 0; i < items.length; i++) {
      html += _storeRowHtml(items[i], installed[items[i].name], i < 5);
      shown++;
    }
  } else if (!_storeState.q && _storeState.cat === '전체') {
    // 카테고리별 구획 헤더 (GPT 가독 구조)
    var byCat = {};
    for (var k = 0; k < items.length; k++) { (byCat[items[k].category] = byCat[items[k].category] || []).push(items[k]); }
    var order = (_storeState.catalog.categories || []).filter(function (c) { return byCat[c]; });
    for (var ci = 0; ci < order.length; ci++) {
      var cat = order[ci];
      var cico = _STORE_CAT_ICONS[cat] ? _STORE_CAT_ICONS[cat] + ' ' : '';
      html += '<div class="pstore-section">' + cico + esc(cat) + '</div>';
      for (var j = 0; j < byCat[cat].length; j++) {
        html += _storeRowHtml(byCat[cat][j], installed[byCat[cat][j].name], false);
        shown++;
      }
    }
  } else {
    for (var m = 0; m < items.length; m++) {
      html += _storeRowHtml(items[m], installed[items[m].name], false);
      shown++;
    }
  }
  grid.innerHTML = html || '<div class="pstore-empty">결과 없음 — 검색어/카테고리를 바꿔보세요</div>';
  if (foot) foot.textContent = '총 ' + shown + '개 표시 · 카탈로그 ' + (_storeState.catalog.plugins || []).length + '개 (갱신: ' + (_storeState.catalog.updated || '?') + ')';
}

