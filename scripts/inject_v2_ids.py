html_path = r'c:\daon\Daon agent System\static\v2\index.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Harness telemetry terminal id
old_term = '<div class="p-space-md font-code text-code text-[12px] space-y-2 max-h-[320px] overflow-y-auto leading-relaxed">'
new_term = '<div class="p-space-md font-code text-code text-[12px] space-y-2 max-h-[320px] overflow-y-auto leading-relaxed" id="harness-telemetry-terminal">'
if old_term in html:
    html = html.replace(old_term, new_term, 1)
    print("1. Injected harness-telemetry-terminal")

# 2. Boardroom transcript container
old_trans = '<div class="p-space-md flex flex-col gap-3 font-body-sm text-[13px]">'
new_trans = '<div class="p-space-md flex flex-col gap-3 font-body-sm text-[13px] max-h-[360px] overflow-y-auto" id="boardroom-transcript-container">'
if old_trans in html:
    html = html.replace(old_trans, new_trans, 1)
    print("2. Injected boardroom-transcript-container")

# 3. Boardroom broadcast button
old_bbtn = '<span>전체 지시 하달</span><span class="material-symbols-outlined text-[15px]">send</span></button>'
new_bbtn = '<span>전체 지시 하달</span><span class="material-symbols-outlined text-[15px]">send</span></button>'
# Find the button wrapping it
bbtn_pos = html.find('<span>전체 지시 하달</span>')
if bbtn_pos != -1:
    btn_start = html.rfind('<button', 0, bbtn_pos)
    html = html[:btn_start] + '<button id="boardroom-broadcast-btn" ' + html[btn_start+8:]
    print("3. Injected boardroom-broadcast-btn")

# 4. MCP servers grid
old_mcp = '<!-- MCP 2-Column Cards Grid --><div class="grid grid-cols-1 md:grid-cols-2 gap-space-md">'
new_mcp = '<!-- MCP 2-Column Cards Grid --><div class="grid grid-cols-1 md:grid-cols-2 gap-space-md" id="mcp-servers-grid">'
if old_mcp in html:
    html = html.replace(old_mcp, new_mcp, 1)
    print("4. Injected mcp-servers-grid")

# 5. Skills search input & catalog grid
skills_header_target = '<div class="px-2 py-0.5 bg-primary text-on-primary font-code text-[11px] font-semibold rounded">SKILL REGISTRY</div><div><h2 class="font-headline-md text-headline-md font-semibold text-on-surface">에이전트 스킬 (Agent Skills &amp; Capability Slots)</h2><p class="font-body-sm text-[12px] text-on-surface-variant">프롬프트 체인, 파이썬 스크립트 기반 실행형 전문 스킬셋</p></div></div>'
skills_header_replacement = '''<div class="px-2 py-0.5 bg-primary text-on-primary font-code text-[11px] font-semibold rounded">SKILL REGISTRY</div><div><h2 class="font-headline-md text-headline-md font-semibold text-on-surface">에이전트 스킬 (Agent Skills &amp; Capability Slots)</h2><p class="font-body-sm text-[12px] text-on-surface-variant">프롬프트 체인, 파이썬 스크립트 기반 실행형 전문 스킬셋</p></div></div><div class="flex items-center gap-2"><div class="flex items-center gap-1 px-3 py-1 bg-surface border border-black/10 rounded-[8px] text-[12px] font-label-md"><span class="material-symbols-outlined text-[16px]">search</span><input class="bg-transparent border-0 outline-none text-on-surface placeholder:text-on-surface-variant/60 w-44" id="skills-search-input" placeholder="345개 스킬 검색..." type="text"/></div></div>'''
if skills_header_target in html:
    html = html.replace(skills_header_target, skills_header_replacement, 1)
    print("5. Injected skills-search-input")

old_skills_grid = '등록된 스킬 라이브러리</h3><div class="grid grid-cols-1 md:grid-cols-2 gap-3">'
new_skills_grid = '등록된 스킬 라이브러리 (<span id="skills-count-label">345개</span>)</h3><div class="grid grid-cols-1 md:grid-cols-2 gap-3" id="skills-catalog-grid">'
if old_skills_grid in html:
    html = html.replace(old_skills_grid, new_skills_grid, 1)
    print("6. Injected skills-catalog-grid")

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)
print("INDEX.HTML ENRICHED SUCCESSFULLY!")
