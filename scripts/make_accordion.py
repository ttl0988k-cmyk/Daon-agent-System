import re

html_path = r'c:\daon\Daon agent System\static\v2\index.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()

# Pattern for model matrix block
target = '<!-- Model & Reasoning Settings Matrix -->'
pos = html.find(target)
if pos != -1:
    end_marker = 'Daon Agent는 중요한 정보를 생성할 때'
    end_pos = html.find(end_marker, pos)
    p_open_pos = html.rfind('<p class=', pos, end_pos)

    new_block = '''<!-- Model & Reasoning Settings Matrix (Accordion) -->
<details class="w-full bg-surface border border-black/10 rounded-[10px] p-space-md mb-1 group transition-all">
<summary class="flex items-center justify-between cursor-pointer list-none select-none">
<div class="flex items-center gap-space-sm">
<span class="material-symbols-outlined text-[16px] text-on-surface-variant">tune</span>
<span class="font-label-md text-label-md text-on-surface font-semibold">실행 모델 &amp; 추론 강도 설정</span>
<span class="text-[11px] font-code px-1.5 py-0.5 bg-black/[0.05] rounded text-on-surface-variant font-medium">Auto-Route</span>
</div>
<div class="flex items-center gap-space-sm">
<span class="font-code text-code text-[11px] text-on-surface-variant">Temp: 0.2</span>
<span class="material-symbols-outlined text-[18px] text-on-surface-variant transition-transform group-open:rotate-180">expand_more</span>
</div>
</summary>
<div class="pt-3 border-t border-black/[0.06] mt-3 flex flex-col gap-space-md">
<div class="grid grid-cols-1 md:grid-cols-3 gap-space-sm">
<div class="p-space-sm bg-surface-container-lowest border-2 border-primary rounded-[10px] flex flex-col justify-between cursor-pointer transition-colors relative model-selection-card" data-model="orchestrator">
<div class="flex items-start justify-between gap-1">
<div class="flex flex-col"><div class="flex items-center gap-1.5"><span class="font-label-md text-label-md text-on-surface font-semibold">Daon Orchestrator</span><span class="px-1.5 py-[1px] bg-primary text-on-primary text-[10px] font-code rounded">기본</span></div><span class="font-code text-code text-[11px] text-on-surface-variant">v2.5 · 128k ctx</span></div>
<span class="material-symbols-outlined text-[16px] text-primary model-radio-icon">radio_button_checked</span>
</div>
<p class="font-body-sm text-body-sm text-on-surface-variant mt-2 leading-tight">멀티 에이전트 분산 라우팅 및 종합 밸런스 표준</p>
</div>
<div class="p-space-sm bg-surface border border-black/10 hover:border-black/30 rounded-[10px] flex flex-col justify-between cursor-pointer transition-colors model-selection-card" data-model="flash">
<div class="flex items-start justify-between gap-1">
<div class="flex flex-col"><div class="flex items-center gap-1.5"><span class="font-label-md text-label-md text-on-surface font-medium">Daon Speed-Flash</span><span class="px-1.5 py-[1px] bg-surface-container-highest text-on-surface text-[10px] font-code rounded">저지연</span></div><span class="font-code text-code text-[11px] text-on-surface-variant">Flash-Lite · 32k ctx</span></div>
<span class="material-symbols-outlined text-[16px] text-on-surface-variant model-radio-icon">radio_button_unchecked</span>
</div>
<p class="font-body-sm text-body-sm text-on-surface-variant mt-2 leading-tight">TTFT 단축 및 신속 질의 응답 최적화</p>
</div>
<div class="p-space-sm bg-surface border border-black/10 hover:border-black/30 rounded-[10px] flex flex-col justify-between cursor-pointer transition-colors model-selection-card" data-model="reasoning">
<div class="flex items-start justify-between gap-1">
<div class="flex flex-col"><div class="flex items-center gap-1.5"><span class="font-label-md text-label-md text-on-surface font-medium">Daon Deep-Reasoning</span><span class="px-1.5 py-[1px] bg-surface-container-highest text-on-surface text-[10px] font-code rounded">심층추론</span></div><span class="font-code text-code text-[11px] text-on-surface-variant">o-series CoT · 256k ctx</span></div>
<span class="material-symbols-outlined text-[16px] text-on-surface-variant model-radio-icon">radio_button_unchecked</span>
</div>
<p class="font-body-sm text-body-sm text-on-surface-variant mt-2 leading-tight">단계별 자기검증 및 복합 아키텍처 심사</p>
</div>
</div>
<div class="flex flex-col md:flex-row md:items-center justify-between gap-space-sm pt-2 border-t border-black/[0.06]">
<div class="flex items-center gap-space-sm"><span class="font-label-sm text-label-sm text-on-surface font-medium flex items-center gap-1"><span class="material-symbols-outlined text-[15px] text-on-surface-variant">psychology</span>추론 강도 (Reasoning Effort)</span>
<div class="flex items-center p-0.5 bg-surface-container border border-black/10 rounded-[8px]">
<button class="px-space-sm py-0.5 font-label-sm text-label-sm text-on-surface-variant hover:text-on-surface rounded-[6px] transition-colors reasoning-effort-btn" data-effort="low" type="button">낮음</button>
<button class="px-space-sm py-0.5 font-label-sm text-label-sm bg-surface-container-lowest text-on-surface font-semibold rounded-[6px] border border-black/10 transition-colors reasoning-effort-btn" data-effort="medium" type="button">중간 (권장)</button>
<button class="px-space-sm py-0.5 font-label-sm text-label-sm text-on-surface-variant hover:text-on-surface rounded-[6px] transition-colors reasoning-effort-btn" data-effort="high" type="button">높음 (Deep)</button>
</div>
</div>
<span class="font-body-sm text-body-sm text-on-surface-variant/80 text-[11px]">추론 깊이가 증가할수록 체계적인 검증을 수행하며 TTFT가 소폭 증가합니다.</span>
</div>
</div>
</details>
'''
    html = html[:pos] + new_block + html[p_open_pos:]
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print("SUCCESS")
else:
    print("Target not found")
