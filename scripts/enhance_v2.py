import re
from pathlib import Path
from bs4 import BeautifulSoup

html_path = Path(r'c:\daon\Daon agent System\static\v2\index.html')
text = html_path.read_text(encoding='utf-8')
soup = BeautifulSoup(text, 'html.parser')

# 1. Header engine status
status_span = soup.find(string=re.compile(r'Daon Workspace v2\.5'))
if status_span:
    parent = status_span.parent
    status_span.replace_with(soup.new_tag('span', id='engine-status-text'))
    parent.find(id='engine-status-text').string = 'Daon Workspace v2.5'
    dot = parent.find('span', class_=re.compile(r'rounded-full'))
    if dot:
        dot['id'] = 'engine-status-dot'

# 2. Export button
export_btn = None
for b in soup.find_all('button'):
    if 'Export' in b.get_text():
        export_btn = b
        break
if export_btn:
    export_btn['id'] = 'export-session-btn'

# 3. New session button in sidebar
new_btn = None
for b in soup.find_all('button'):
    if 'New session' in b.get_text():
        new_btn = b
        break
if new_btn:
    classes = new_btn.get('class', [])
    if 'new-session-btn' not in classes:
        classes.append('new-session-btn')
        new_btn['class'] = classes
    new_btn['data-action'] = 'new-session'

# 4. Recent threads container
recent_label = soup.find(string=re.compile(r'Recent Threads'))
if recent_label:
    parent_box = recent_label.find_parent('div', class_=re.compile(r'flex-col'))
    if parent_box:
        links_div = parent_box.find('div', class_=re.compile(r'gap-\[2px\]'))
        if links_div:
            links_div['id'] = 'recent-threads-list'

# 5. Tab Chat: session meta & messages container
sess_meta = soup.find(string=re.compile(r'SESSION #DAON-'))
if sess_meta:
    meta_span = sess_meta.parent
    meta_span['id'] = 'session-meta-id'

tab_chat = soup.find(id='tab-chat-session')
if tab_chat:
    articles = tab_chat.find_all('article')
    if articles:
        parent_stream = articles[0].parent
        wrapper = soup.new_tag('div', id='chat-messages-container', **{'class': 'flex flex-col w-full gap-space-2xl'})
        parent_stream.insert(0, wrapper)
        for a in list(articles):
            wrapper.append(a.extract())

# 6. Composer container
chat_input = soup.find(id='chat-input')
if chat_input:
    composer_wrapper = chat_input.find_parent('div', class_=re.compile(r'fixed bottom-0'))
    if composer_wrapper:
        classes = composer_wrapper.get('class', [])
        if 'floating-composer-container' not in classes:
            classes.append('floating-composer-container')
            composer_wrapper['class'] = classes

# 7. Model selection cards
model_names = ['Daon Orchestrator', 'Daon Speed-Flash', 'Daon Deep-Reasoning']
model_keys = ['orchestrator', 'flash', 'reasoning']
for mname, mkey in zip(model_names, model_keys):
    el = soup.find(string=re.compile(mname))
    if el:
        card = el.find_parent('div', class_=re.compile(r'rounded-\[10px\]'))
        if card:
            classes = card.get('class', [])
            if 'model-selection-card' not in classes:
                classes.append('model-selection-card')
                card['class'] = classes
            card['data-model'] = mkey
            icon = card.find('span', string=re.compile(r'radio_button'))
            if icon:
                iclasses = icon.get('class', [])
                if 'model-radio-icon' not in iclasses:
                    iclasses.append('model-radio-icon')
                    icon['class'] = iclasses

# 8. Reasoning effort buttons
for eff_name, eff_key in [('낮음', 'low'), ('중간', 'medium'), ('높음', 'high')]:
    btn = soup.find('button', string=re.compile(eff_name))
    if btn:
        classes = btn.get('class', [])
        if 'reasoning-effort-btn' not in classes:
            classes.append('reasoning-effort-btn')
            btn['class'] = classes
        btn['data-effort'] = eff_key

# 9. Dynamic Harness steps and telemetry
tab_harness = soup.find(id='tab-dynamic-harness')
if tab_harness:
    for i in range(1, 5):
        s_span = tab_harness.find(string=re.compile(f'STEP 0{i}'))
        if s_span:
            card = s_span.find_parent('div', class_=re.compile(r'rounded-\[8px\]'))
            if card:
                card['id'] = f'harness-step-{i}'
                badge = card.find('span', string=re.compile(r'DONE|IN PROGRESS|PENDING'))
                if badge:
                    classes = badge.get('class', [])
                    if 'step-badge' not in classes:
                        classes.append('step-badge')
                        badge['class'] = classes

    telemetry_header = tab_harness.find(string=re.compile(r'하네스 실시간 텔레메트리'))
    if telemetry_header:
        box = telemetry_header.find_parent('div', class_=re.compile(r'border'))
        if box:
            log_div = box.find('div', class_=re.compile(r'overflow-y-auto'))
            if log_div:
                log_div['id'] = 'harness-telemetry-terminal'

    # Add quick task launch bar if not present
    if not tab_harness.find(id='harness-task-input'):
        first_panel = tab_harness.find('div', class_=re.compile(r'bg-surface border'))
        if first_panel:
            launch_bar_html = '''
              <div class="p-space-md border border-black/10 rounded-[12px] bg-surface flex flex-col md:flex-row items-center justify-between gap-3 mb-2">
                <div class="flex items-center gap-2 flex-1 w-full">
                  <span class="material-symbols-outlined text-[20px] text-on-surface-variant">terminal</span>
                  <input id="harness-task-input" type="text" placeholder="하네스 실행 작업 또는 리팩토링 목표 입력..." class="flex-1 bg-surface-container-lowest border border-black/10 rounded-[8px] px-3 py-1.5 font-body-sm text-body-sm text-on-surface outline-none focus:border-black transition-colors" />
                </div>
                <button id="harness-start-btn" type="button" class="w-full md:w-auto px-4 py-1.5 bg-primary text-on-primary rounded-[8px] font-label-md text-label-md font-medium hover:bg-black/80 transition-colors flex items-center justify-center gap-1.5 cursor-pointer">
                  <span class="material-symbols-outlined text-[16px]">play_arrow</span>
                  <span>새 하네스 작업 시작</span>
                </button>
              </div>
            '''
            launch_bar = BeautifulSoup(launch_bar_html, 'html.parser')
            first_panel.insert_before(launch_bar)

# 10. Boardroom cards
tab_board = soup.find(id='tab-agent-boardroom')
if tab_board:
    for h4 in tab_board.find_all('h4'):
        agent_name = h4.get_text(strip=True)
        card = h4.find_parent('div', class_=re.compile(r'rounded-\[10px\]'))
        if card and not card.find('button', class_='agent-dispatch-btn'):
            btn_html = f'''
              <button type="button" class="agent-dispatch-btn w-full mt-2 py-1 px-2 border border-black/10 rounded-[6px] font-label-sm text-[11px] text-on-surface hover:bg-black/[0.05] transition-colors flex items-center justify-center gap-1 cursor-pointer" data-agent="{agent_name}">
                <span class="material-symbols-outlined text-[13px]">send</span>
                <span>작업 지시</span>
              </button>
            '''
            btn = BeautifulSoup(btn_html, 'html.parser')
            card.append(btn)

# 11. MCP & Skills search inputs & classes
tab_mcp = soup.find(id='tab-plugin-mcp')
if tab_mcp:
    mcp_inp = tab_mcp.find('input')
    if mcp_inp:
        mcp_inp['id'] = 'mcp-search-input'
    inst_h2 = tab_mcp.find(string=re.compile(r'설치됨|Installed'))
    if inst_h2:
        parent_sec = inst_h2.find_parent('div')
        if parent_sec:
            cards_wrap = parent_sec.find_next_sibling('div')
            if cards_wrap:
                cards_wrap['id'] = 'installed-mcp-container'
    for c in tab_mcp.find_all('div', class_=re.compile(r'rounded-\[10px\]')):
        classes = c.get('class', [])
        if 'mcp-plugin-card' not in classes:
            classes.append('mcp-plugin-card')
            c['class'] = classes

tab_skills = soup.find(id='tab-agent-skills')
if tab_skills:
    sk_inp = tab_skills.find('input')
    if sk_inp:
        sk_inp['id'] = 'skills-search-input'
    for c in tab_skills.find_all('div', class_=re.compile(r'rounded-\[10px\]')):
        classes = c.get('class', [])
        if 'skill-catalog-card' not in classes:
            classes.append('skill-catalog-card')
            c['class'] = classes

# 12. Replace inline script at bottom with module import
for s in soup.find_all('script'):
    if s.string and 'Vanilla JS Tab Switching' in s.string:
        s.decompose()

# Append module script before </body>
app_script = soup.new_tag('script', type='module', src='/static/v2/js/app.js')
soup.body.append(app_script)

html_path.write_text(str(soup), encoding='utf-8')
print('Successfully enhanced static/v2/index.html!')
