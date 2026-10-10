#!/usr/bin/env python3
"""
Enhance static/v2/index.html with:
1. Mobile responsive sidebar drawer (hamburger button, overlay backdrop, close button, pl-0 md:pl-[270px], left-0 md:left-[270px])
2. Session delete buttons (individual delete trash button, delete all button)
3. Provider & Model slide/accordion drawer above user settings
4. Attachment file input and tray (image, video, file)
"""

import sys
from pathlib import Path

index_path = Path(r"c:\daon\Daon agent System\static\v2\index.html")
html = index_path.read_text(encoding="utf-8")

# 1. Mobile backdrop and aside class
if '<div id="sidebar-backdrop"' not in html:
    html = html.replace(
        '<body class="bg-surface-container-lowest font-body-md text-on-surface antialiased"><aside class="fixed left-0 top-0 h-full w-[270px] bg-surface border-r border-black/10 z-40 flex flex-col justify-between select-none">',
        '<body class="bg-surface-container-lowest font-body-md text-on-surface antialiased"><div id="sidebar-backdrop" class="fixed inset-0 bg-black/50 z-30 hidden md:hidden transition-opacity"></div><aside id="app-sidebar" class="fixed left-0 top-0 h-full w-[280px] sm:w-[270px] bg-surface border-r border-black/10 z-40 flex flex-col justify-between select-none transition-transform duration-300 -translate-x-full md:translate-x-0">'
    )

# 2. Sidebar header close button for mobile
if 'id="sidebar-close-btn"' not in html:
    html = html.replace(
        '<div class="flex items-center text-on-surface-variant font-label-sm text-label-sm px-space-xs py-[2px] bg-surface-variant/40 rounded">v2.5</div></div><button class="w-full h-9 flex items-center justify-between px-space-md',
        '<div class="flex items-center gap-1.5"><div class="flex items-center text-on-surface-variant font-label-sm text-label-sm px-space-xs py-[2px] bg-surface-variant/40 rounded">v2.5</div><button id="sidebar-close-btn" class="md:hidden p-1 rounded hover:bg-black/5 text-on-surface-variant flex items-center justify-center cursor-pointer" type="button" title="사이드바 닫기"><span class="material-symbols-outlined text-[18px]">close</span></button></div></div><button class="w-full h-9 flex items-center justify-between px-space-md'
    )

# 3. Recent Threads header delete all button
if 'id="delete-all-sessions-btn"' not in html:
    html = html.replace(
        '<div class="px-space-sm font-label-sm text-label-sm text-on-surface-variant/70 uppercase tracking-wider mb-space-xs">Recent Threads</div>',
        '<div class="flex items-center justify-between px-space-sm mb-space-xs"><span class="font-label-sm text-label-sm text-on-surface-variant/70 uppercase tracking-wider">Recent Threads</span><button id="delete-all-sessions-btn" title="모든 세션 삭제" class="text-[11px] font-label-sm text-on-surface-variant/70 hover:text-red-600 flex items-center gap-0.5 transition-colors cursor-pointer" type="button"><span class="material-symbols-outlined text-[13px]">delete_sweep</span><span>전체삭제</span></button></div>'
    )

# 4. Provider & Model Drawer above user settings
if 'id="provider-drawer-container"' not in html:
    drawer_html = '''<!-- Provider & Model Selection Slide Drawer -->
<div class="px-space-md py-2 border-t border-black/10 bg-surface flex flex-col gap-1.5" id="provider-drawer-container">
  <button id="provider-drawer-toggle" class="w-full flex items-center justify-between py-1 px-1.5 rounded-[8px] hover:bg-black/[0.04] transition-colors cursor-pointer select-none text-left" type="button">
    <div class="flex items-center gap-2 min-w-0">
      <span class="material-symbols-outlined text-[17px] text-on-surface-variant shrink-0">smart_toy</span>
      <div class="flex flex-col min-w-0">
        <span class="text-[10px] font-code text-on-surface-variant truncate uppercase tracking-wider" id="current-provider-label">PROVIDER: OpenCode Go</span>
        <span class="text-[12px] font-semibold text-on-surface truncate" id="current-model-label">deepseek-v4.1-flash</span>
      </div>
    </div>
    <span class="material-symbols-outlined text-[18px] text-on-surface-variant transition-transform" id="provider-drawer-arrow">expand_more</span>
  </button>
  <div id="provider-drawer-content" class="hidden flex-col gap-2 pt-2 border-t border-black/[0.06] max-h-[220px] overflow-y-auto">
    <div class="text-[11px] font-semibold text-on-surface-variant px-1">프로바이더 선택</div>
    <div class="flex items-center gap-1 overflow-x-auto pb-1" id="provider-pills-list"></div>
    <div class="text-[11px] font-semibold text-on-surface-variant px-1 mt-1">모델 선택</div>
    <div class="flex flex-col gap-1" id="provider-models-list"></div>
  </div>
</div>
'''
    html = html.replace(
        '<div class="p-space-md border-t border-black/10 flex flex-col gap-space-sm bg-surface"><div class="flex items-center justify-between px-space-sm py-space-xs"><a class="flex items-center gap-space-sm group cursor-pointer"',
        drawer_html + '<div class="p-space-md border-t border-black/10 flex flex-col gap-space-sm bg-surface"><div class="flex items-center justify-between px-space-sm py-space-xs"><a class="flex items-center gap-space-sm group cursor-pointer"'
    )

# 5. Header and layout padding
html = html.replace(
    '<div class="pl-[270px] min-h-screen flex flex-col bg-surface-container-lowest">',
    '<div class="pl-0 md:pl-[270px] min-h-screen flex flex-col bg-surface-container-lowest">'
)

html = html.replace(
    '<header class="fixed top-0 left-[270px] right-0 h-14 bg-surface-container-lowest border-b border-black/10 z-30 flex items-center justify-between px-space-xl">',
    '<header class="fixed top-0 left-0 md:left-[270px] right-0 h-14 bg-surface-container-lowest border-b border-black/10 z-20 flex items-center justify-between px-space-md md:px-space-xl">'
)

if 'id="sidebar-toggle-btn"' not in html:
    html = html.replace(
        '<div class="flex items-center gap-space-md min-w-0"><div class="flex items-center gap-space-xs border border-black/10 rounded-[6px] px-space-sm py-[3px] bg-surface">',
        '<div class="flex items-center gap-space-md min-w-0"><button id="sidebar-toggle-btn" class="md:hidden p-1.5 rounded-[8px] hover:bg-black/5 text-on-surface flex items-center justify-center shrink-0 cursor-pointer" type="button" title="메뉴 열기"><span class="material-symbols-outlined text-[22px]">menu</span></button><div class="flex items-center gap-space-xs border border-black/10 rounded-[6px] px-space-sm py-[3px] bg-surface">'
    )

html = html.replace(
    '<nav class="fixed top-14 left-[270px] right-0 h-11 bg-surface border-b border-black/10 z-20 flex items-center px-space-lg overflow-x-auto">',
    '<nav class="fixed top-14 left-0 md:left-[270px] right-0 h-11 bg-surface border-b border-black/10 z-10 flex items-center px-space-md md:px-space-lg overflow-x-auto">'
)

html = html.replace(
    '<div class="fixed bottom-0 left-[270px] right-0 z-20 pointer-events-none pb-4 pt-8 bg-gradient-to-t from-surface-container-lowest via-surface-container-lowest/95 to-transparent floating-composer-container">',
    '<div class="fixed bottom-0 left-0 md:left-[270px] right-0 z-20 pointer-events-none pb-4 pt-8 bg-gradient-to-t from-surface-container-lowest via-surface-container-lowest/95 to-transparent floating-composer-container">'
)

# 6. Attachment file input and tray in composer
if 'id="chat-attach-tray"' not in html:
    html = html.replace(
        '<div class="w-full bg-surface-container-lowest border border-black/10 rounded-[12px] p-space-sm flex flex-col gap-space-sm focus-within:border-black transition-colors"><div class="w-full"><textarea class="w-full bg-transparent resize-none border-0 outline-none text-on-surface placeholder:text-on-surface-variant/60 font-body-md text-body-md leading-relaxed px-1" id="chat-input"',
        '<div class="w-full bg-surface-container-lowest border border-black/10 rounded-[12px] p-space-sm flex flex-col gap-space-sm focus-within:border-black transition-colors"><input type="file" id="chat-file-input" multiple accept="image/*,video/*,.pdf,.txt,.py,.js,.html,.json,.csv,.zip" class="hidden" /><div id="chat-attach-tray" class="hidden flex-wrap gap-2 px-1 pt-1 pb-1 border-b border-black/[0.06]"></div><div class="w-full"><textarea class="w-full bg-transparent resize-none border-0 outline-none text-on-surface placeholder:text-on-surface-variant/60 font-body-md text-body-md leading-relaxed px-1" id="chat-input"'
    )

html = html.replace(
    'title="파일 및 참조 문서 첨부" type="button"><span class="material-symbols-outlined text-[18px]">attach_file</span></button>',
    'id="chat-attach-btn" title="파일, 이미지, 영상 첨부" type="button" class="w-8 h-8 flex items-center justify-center rounded-[8px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.05] transition-colors cursor-pointer"><span class="material-symbols-outlined text-[18px]">attach_file</span></button>'
)

index_path.write_text(html, encoding="utf-8")
print("Successfully enhanced static/v2/index.html")
