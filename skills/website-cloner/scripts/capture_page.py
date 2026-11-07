#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
capture_page.py — 렌더 기반 캡처 + 계측 (website-cloner 스킬)

실제 Chrome으로 페이지를 열어:
  · 뷰포트별 스크린샷(전체/뷰포트 단위) 생성
  · 지정 셀렉터의 기하 + computed style 선언값을 JSON으로 덤프
  · 뷰포트 폭 스윕으로 실제 브레이크포인트 탐지
  · 콘솔 에러 / pageerror / 실패 요청(4xx·5xx) 수집
  · (옵션) hover/클릭 상태 스크린샷

사용:
  python capture_page.py https://example.com --out docs/design-references \
      --viewports 1440x900,768x1024,390x844 --fullpage \
      --measure "header,nav,h1,.hero,footer"

  # 브레이크포인트 탐지
  python capture_page.py https://example.com --out shots --sweep "h1" --sweep-range 320,1920,20

  # 상태 캡처
  python capture_page.py https://example.com --out shots --hover ".card:first-child" \
      --click "button.accordion:first-child"

전제:
  · 시스템 Chrome 사용(channel="chrome"). 없으면 Playwright 번들 chromium 시도.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

MEASURE_JS = """
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return { sel, found: false };
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const pseudo = (p) => { const s = getComputedStyle(el, p);
    return { content: s.content, color: s.color, fontSize: s.fontSize }; };
  return {
    sel, found: true,
    tag: el.tagName.toLowerCase(),
    text: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
    rect: { x: +r.x.toFixed(2), y: +r.y.toFixed(2), w: +r.width.toFixed(2), h: +r.height.toFixed(2) },
    box: { padding: cs.padding, margin: cs.margin, border: cs.border, borderRadius: cs.borderRadius },
    font: { family: cs.fontFamily, size: cs.fontSize, weight: cs.fontWeight,
            lineHeight: cs.lineHeight, letterSpacing: cs.letterSpacing,
            textTransform: cs.textTransform },
    paint: { color: cs.color, backgroundColor: cs.backgroundColor,
             backgroundImage: cs.backgroundImage.slice(0, 180), opacity: cs.opacity },
    layout: { display: cs.display, position: cs.position, gap: cs.gap,
              gridTemplateColumns: cs.gridTemplateColumns, flexDirection: cs.flexDirection,
              maxWidth: cs.maxWidth, zIndex: cs.zIndex },
    motion: { transform: cs.transform, transition: cs.transition,
              animationName: cs.animationName, animationDuration: cs.animationDuration },
    pseudoBefore: pseudo('::before'),
  };
}
"""

SWEEP_JS = """
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const cs = getComputedStyle(el);
  // gridTemplateColumns / padding 등은 폭에 따라 픽셀로 재계산되어 노이즈가 된다.
  // '불연속' 성격만 남긴다: 열 개수, display, flex 방향, 폰트 크기, 줄 높이.
  return {
    fontSize: cs.fontSize,
    lineHeight: cs.lineHeight,
    display: cs.display,
    flexDirection: cs.flexDirection,
    gridColumns: cs.gridTemplateColumns === 'none' ? 'none'
                 : String(cs.gridTemplateColumns).split(' ').length + 'cols',
    visibility: cs.visibility,
    position: cs.position
  };
}
"""

OVERFLOW_JS = """
() => {
  const de = document.documentElement;
  const over = [];
  document.querySelectorAll('body *').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && (r.right > de.clientWidth + 1 || r.left < -1)) {
      over.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className && String(el.className).slice(0, 60)) || '',
        left: +r.left.toFixed(1), right: +r.right.toFixed(1), w: +r.width.toFixed(1)
      });
    }
  });
  return { scrollWidth: de.scrollWidth, clientWidth: de.clientWidth,
           hasHorizontalOverflow: de.scrollWidth > de.clientWidth + 1,
           offenders: over.slice(0, 25) };
}
"""

FONTS_JS = """
async () => {
  await document.fonts.ready;
  const used = {};
  for (const f of document.fonts) {
    used[f.family + '|' + f.weight + '|' + f.style] = {
      family: f.family, weight: f.weight, style: f.style, status: f.status
    };
  }
  return Object.values(used);
}
"""


def parse_viewports(spec: str):
    out = []
    for part in spec.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if "x" not in part:
            raise SystemExit(f"뷰포트 형식 오류: {part!r} (예: 1440x900)")
        w, h = part.split("x", 1)
        out.append((int(w), int(h)))
    return out or [(1440, 900)]


def slugify(s: str) -> str:
    return re.sub(r"[^\w.-]+", "_", s)[:60].strip("_") or "x"


def launch(pw):
    """시스템 Chrome 우선, 실패하면 Playwright 번들 chromium."""
    errors = []
    for kwargs in ({"channel": "chrome"}, {}):
        try:
            return pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"], **kwargs)
        except Exception as e:
            errors.append(f"{kwargs or 'bundled'}: {type(e).__name__}: {e}")
    raise SystemExit("브라우저 실행 실패:\n  " + "\n  ".join(errors) +
                     "\n→ `python -m playwright install chromium` 실행 필요")


def preload_lazy(page, step=800, max_steps=80, pause=180):
    """휠 스크롤로 lazy 미디어·리빌 콘텐츠를 실제로 노출시킨다."""
    last_h = 0
    for _ in range(max_steps):
        page.mouse.wheel(0, step)
        page.wait_for_timeout(pause)
        h = page.evaluate("document.body.scrollHeight")
        y = page.evaluate("window.scrollY")
        if y + page.viewport_size["height"] >= h - 5 and h == last_h:
            break
        last_h = h
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)


def collect_listeners(page, bucket):
    page.on("console", lambda m: bucket["console"].append(
        {"type": m.type, "text": m.text[:400]}))
    page.on("pageerror", lambda e: bucket["pageerror"].append(str(e)[:400]))
    page.on("requestfailed", lambda r: bucket["requestfailed"].append(
        {"url": r.url, "reason": (r.failure or "")[:160]}))
    page.on("response", lambda r: bucket["http_errors"].append(
        {"url": r.url, "status": r.status}) if r.status >= 400 else None)


def main():
    ap = argparse.ArgumentParser(description="렌더 기반 캡처 + computed style 계측")
    ap.add_argument("url")
    ap.add_argument("--out", required=True)
    ap.add_argument("--viewports", default="1440x900,390x844")
    ap.add_argument("--fullpage", action="store_true", help="전체 페이지 캡처(스크롤 프리로드 후)")
    ap.add_argument("--measure", default="", help="계측할 CSS 셀렉터(콤마 구분)")
    ap.add_argument("--sweep", default="", help="브레이크포인트 탐지용 셀렉터")
    ap.add_argument("--sweep-range", default="320,1920,20", help="시작,끝,간격")
    ap.add_argument("--hover", action="append", default=[], help="hover 상태 캡처 셀렉터")
    ap.add_argument("--click", action="append", default=[], help="클릭 상태 캡처 셀렉터")
    ap.add_argument("--wait", type=int, default=800, help="정착 대기 ms")
    ap.add_argument("--timeout", type=int, default=90000)
    ap.add_argument("--label", default="", help="파일 접두어(기본: 호스트명)")
    ap.add_argument("--no-preload", dest="preload", action="store_false",
                    help="lazy 스크롤 프리로드 생략")
    ap.set_defaults(preload=True)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    os.makedirs(args.out, exist_ok=True)
    label = args.label or slugify(re.sub(r"^https?://", "", args.url).split("/")[0])
    viewports = parse_viewports(args.viewports)
    selectors = [s.strip() for s in args.measure.split(",") if s.strip()]

    report = {"url": args.url, "label": label, "viewports": {}, "sweep": None,
              "runtime": {}, "overflow": {}, "files": []}

    with sync_playwright() as pw:
        browser = launch(pw)
        for (w, h) in viewports:
            ctx = browser.new_context(
                viewport={"width": w, "height": h},
                device_scale_factor=1,
                reduced_motion="no-preference",
                ignore_https_errors=True,
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"),
            )
            page = ctx.new_page()
            bucket = {"console": [], "pageerror": [], "requestfailed": [], "http_errors": []}
            collect_listeners(page, bucket)

            try:
                page.goto(args.url, wait_until="networkidle", timeout=args.timeout)
            except Exception as e:
                print(f"[WARN] networkidle 실패({type(e).__name__}), load로 재시도")
                page.goto(args.url, wait_until="load", timeout=args.timeout)

            try:
                page.evaluate("async () => { if (document.fonts) await document.fonts.ready; }")
            except Exception:
                pass
            page.wait_for_timeout(args.wait)

            if args.preload:
                preload_lazy(page)

            # 스크린샷
            shot = os.path.join(args.out, f"{label}_{w}x{h}.png")
            page.screenshot(path=shot, full_page=False)
            report["files"].append(shot)
            if args.fullpage:
                shot_full = os.path.join(args.out, f"{label}_{w}x{h}_full.png")
                page.screenshot(path=shot_full, full_page=True)
                report["files"].append(shot_full)

            # 계측
            measured = {}
            for sel in selectors:
                try:
                    measured[sel] = page.evaluate(MEASURE_JS, sel)
                except Exception as e:
                    measured[sel] = {"sel": sel, "error": str(e)[:200]}

            # 폰트/오버플로/런타임
            try:
                fonts = page.evaluate(FONTS_JS)
            except Exception:
                fonts = []
            try:
                overflow = page.evaluate(OVERFLOW_JS)
            except Exception as e:
                overflow = {"error": str(e)[:200]}

            report["viewports"][f"{w}x{h}"] = {
                "screenshot": shot, "measured": measured, "fonts_loaded": fonts,
            }
            report["overflow"][f"{w}x{h}"] = overflow
            report["runtime"][f"{w}x{h}"] = {
                "console_errors": [c for c in bucket["console"] if c["type"] == "error"],
                "console_warnings": len([c for c in bucket["console"] if c["type"] == "warning"]),
                "pageerror": bucket["pageerror"],
                "requestfailed": bucket["requestfailed"],
                "http_errors": bucket["http_errors"][:50],
            }

            # hover / click 상태
            for sel in args.hover:
                try:
                    page.hover(sel, timeout=5000)
                    page.wait_for_timeout(400)
                    f = os.path.join(args.out, f"{label}_{w}x{h}_hover_{slugify(sel)}.png")
                    page.screenshot(path=f, full_page=False)
                    report["files"].append(f)
                    page.mouse.move(5, 5)
                except Exception as e:
                    print(f"[WARN] hover 실패 {sel}: {e}")

            for sel in args.click:
                try:
                    page.click(sel, timeout=5000)
                    page.wait_for_timeout(500)
                    f = os.path.join(args.out, f"{label}_{w}x{h}_click_{slugify(sel)}.png")
                    page.screenshot(path=f, full_page=False)
                    report["files"].append(f)
                except Exception as e:
                    print(f"[WARN] click 실패 {sel}: {e}")

            errs = report["runtime"][f"{w}x{h}"]
            print(f"[{w}x{h}] shot={os.path.basename(shot)} "
                  f"measure={len([m for m in measured.values() if m.get('found')])}/{len(selectors)} "
                  f"errors={len(errs['console_errors'])+len(errs['pageerror'])} "
                  f"http4xx5xx={len(errs['http_errors'])} "
                  f"overflow={'YES' if overflow.get('hasHorizontalOverflow') else 'no'}")

            ctx.close()

        # 브레이크포인트 스윕
        if args.sweep:
            try:
                start, end, step = [int(x) for x in args.sweep_range.split(",")]
            except Exception:
                start, end, step = 320, 1920, 20
            ctx = browser.new_context(viewport={"width": start, "height": 900},
                                      ignore_https_errors=True)
            page = ctx.new_page()
            page.goto(args.url, wait_until="load", timeout=args.timeout)
            page.wait_for_timeout(args.wait)
            samples = []
            prev = None
            for w in range(start, end + 1, step):
                page.set_viewport_size({"width": w, "height": 900})
                page.wait_for_timeout(120)
                try:
                    st = page.evaluate(SWEEP_JS, args.sweep)
                except Exception:
                    st = None
                changed = bool(prev is not None and st is not None and st != prev)
                samples.append({"width": w, "state": st, "changed": changed})
                prev = st
            breaks = [s["width"] for s in samples if s["changed"]]
            # 연속된 변화 폭은 하나의 전환 구간으로 병합 (px 재계산 노이즈 제거)
            transitions = []
            for b in breaks:
                if transitions and (b - transitions[-1][1]) <= step:
                    transitions[-1][1] = b
                else:
                    transitions.append([b, b])
            trans = [{"start": a, "end": b} for a, b in transitions]
            report["sweep"] = {"selector": args.sweep, "range": [start, end, step],
                               "breakpoints": breaks, "transitions": trans,
                               "samples": samples}
            print(f"[sweep] {args.sweep} transitions={[f'{t['start']}~{t['end']}' for t in trans]}")
            ctx.close()

        browser.close()

    rpath = os.path.join(args.out, f"{label}_report.json")
    with open(rpath, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    total_err = sum(len(v["console_errors"]) + len(v["pageerror"]) for v in report["runtime"].values())
    total_404 = sum(len(v["http_errors"]) for v in report["runtime"].values())
    print(f"\nreport: {rpath}")
    print(f"files: {len(report['files'])}  total_console_errors={total_err}  total_http_errors={total_404}")


if __name__ == "__main__":
    main()
