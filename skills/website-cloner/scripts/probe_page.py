#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_page.py — 웹페이지 정적 1차 수집기 (website-cloner 스킬)

렌더링 없이 HTTP로 원본 HTML/CSS를 받아 다음을 뽑아낸다:
  - 페이지 메타(title/description/og/canonical/lang)
  - 스타일시트 목록 + @font-face(패밀리/굵기/스타일/실제 URL)
  - 이미지/비디오/아이콘/스크립트 URL (src, srcset, data-src, <source>)
  - CSS 안의 url() 참조(배경이미지 등) 및 @import 재귀 1단계
  - :root CSS 커스텀 프로퍼티(--var) 선언값

산출물:
  <out>/page.html        원본 HTML(그대로)
  <out>/manifest.json    download_assets.py 입력용 매니페스트
  <out>/summary.txt      사람이 읽는 요약

사용:
  python probe_page.py https://example.com --out docs/research/example
  python probe_page.py https://a.com https://a.com/about --out docs/research/a --sitemap

주의:
  · 추출은 '선언된 값'이다. 실제 렌더 결과(computed style)는 capture_page.py로 확인한다.
  · CDN은 확장자를 속일 수 있다. 실제 포맷 판정은 download_assets.py가 매직바이트로 한다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from html import unescape
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

try:  # Windows 콘솔 한글 깨짐 방지
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

# ---------------- 정규식 ----------------
LINK_RE = re.compile(r"<link\b([^>]*)>", re.I)
META_RE = re.compile(r"<meta\b([^>]*)>", re.I)
IMG_RE = re.compile(r"<img\b([^>]*)>", re.I)
SOURCE_RE = re.compile(r"<source\b([^>]*)>", re.I)
VIDEO_RE = re.compile(r"<video\b([^>]*)>", re.I)
STYLE_BLOCK_RE = re.compile(r"<style\b([^>]*)>(.*?)</style>", re.S | re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
HTML_LANG_RE = re.compile(r"<html\b([^>]*)>", re.I)
SVG_RE = re.compile(r"<svg\b", re.I)
ATTR_RE = re.compile(r"""([\w:.-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")
CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", re.I)
FONT_FACE_RE = re.compile(r"@font-face\s*{([^}]*)}", re.S | re.I)
IMPORT_RE = re.compile(r"""@import\s+(?:url\(\s*['"]?([^'")]+)['"]?\s*\)|['"]([^'"]+)['"])""", re.I)
ROOT_BLOCK_RE = re.compile(r"(:root|html|body)\s*{([^}]*)}", re.I)
CUSTOM_PROP_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")

ASSET_EXT = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".avif": "image", ".gif": "image", ".svg": "svg", ".bmp": "image",
    ".ico": "icon", ".mp4": "video", ".webm": "video", ".mov": "video",
    ".m4v": "video", ".woff2": "font", ".woff": "font", ".ttf": "font",
    ".otf": "font", ".eot": "font", ".css": "stylesheet", ".js": "script",
    ".mjs": "script", ".json": "data", ".mp3": "audio", ".wav": "audio",
}


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(s or "")).strip()


def parse_attrs(raw: str) -> dict:
    out = {}
    for m in ATTR_RE.finditer(raw):
        key = m.group(1).lower()
        val = m.group(2).strip()
        if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
            val = val[1:-1]
        out[key] = unescape(val)
    return out


def absolute(base: str, url: str) -> str:
    if not url:
        return ""
    url = url.strip().strip("\"'")
    if url.startswith(("data:", "blob:", "javascript:", "mailto:", "tel:", "#")):
        return ""
    return urljoin(base, url)


def split_srcset(value: str) -> list:
    """'a.jpg 1x, b.jpg 2x' -> ['a.jpg','b.jpg'] (후보 전부)"""
    out = []
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        cand = part.split()[0]
        if cand:
            out.append(cand)
    return out


def kind_of(url: str, fallback: str = "other") -> str:
    ext = os.path.splitext(urlsplit(url).path)[1].lower()
    return ASSET_EXT.get(ext, fallback)


class Probe:
    def __init__(self, args):
        self.args = args
        self.fonts: list = []
        self.css_vars: dict = {}
        self.stylesheets: list = []
        self.extra_sitemaps: list = []
        self._css_done: set = set()
        self.sess = requests.Session()
        self.sess.headers.update({
            "User-Agent": args.ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        for h in args.header or []:
            if ":" in h:
                k, v = h.split(":", 1)
                self.sess.headers[k.strip()] = v.strip()
        self.pages: list = []
        self.asset_index: dict = {}
        self.failed: list = []

    # ---- 유틸 ----
    def add_asset(self, url: str, kind: str, note: str = "", referer: str = ""):
        if not url:
            return
        url = url.split("#")[0]
        rec = self.asset_index.get(url)
        if rec:
            if note and note not in rec["notes"]:
                rec["notes"].append(note)
            return
        self.asset_index[url] = {
            "url": url, "kind": kind, "notes": [note] if note else [],
            "referer": referer, "page": "",
        }

    def _encoding_of(self, r) -> str:
        """charset 헤더 → <meta charset> → apparent_encoding 순으로 판정.
        http.server처럼 charset을 안 보내는 서버에서 requests가 ISO-8859-1로
        디코드해 한글이 깨지는 문제를 막는다."""
        ct = r.headers.get("content-type", "").lower()
        m = re.search(r"charset=[\"']?([\w\-]+)", ct)
        if m:
            return m.group(1)
        m2 = re.search(rb"<meta[^>]+charset=[\"']?([\w\-]+)", r.content[:4096], re.I)
        if m2:
            return m2.group(1).decode("ascii", "ignore")
        return r.apparent_encoding or "utf-8"

    def get(self, url: str, as_bytes: bool = False):
        try:
            r = self.sess.get(url, timeout=self.args.timeout, allow_redirects=True)
        except Exception as e:
            self.failed.append({"url": url, "error": f"{type(e).__name__}: {e}"})
            return None
        if r.status_code >= 400:
            self.failed.append({"url": url, "error": f"HTTP {r.status_code}"})
            return None
        if as_bytes:
            return r
        if "charset" not in r.headers.get("content-type", "").lower() and \
                ("text" in r.headers.get("content-type", "").lower() or not r.headers.get("content-type")):
            r.encoding = self._encoding_of(r)
        return r

    # ---- CSS ----
    def parse_css(self, css: str, css_base: str, depth: int = 0, seen: set | None = None):
        seen = seen or set()

        # @font-face
        for block in FONT_FACE_RE.findall(css):
            fam = re.search(r"font-family\s*:\s*([^;]+);", block, re.I)
            weight = re.search(r"font-weight\s*:\s*([^;]+);", block, re.I)
            style = re.search(r"font-style\s*:\s*([^;]+);", block, re.I)
            srcs = CSS_URL_RE.findall(block)
            for src in srcs:
                u = absolute(css_base, src)
                if not u:
                    continue
                self.add_asset(u, "font", "font-face", css_base)
                self.fonts.append({
                    "family": clean_text(fam.group(1)).strip("\"'") if fam else "",
                    "weight": clean_text(weight.group(1)) if weight else "",
                    "style": clean_text(style.group(1)) if style else "",
                    "url": u,
                })

        # url() 참조 (폰트 제외 중복은 add_asset이 흡수)
        for u in CSS_URL_RE.findall(css):
            au = absolute(css_base, u)
            if au and kind_of(au) in ("image", "svg", "video", "audio", "font"):
                self.add_asset(au, kind_of(au), "css-url", css_base)

        # :root 변수
        for sel, body in ROOT_BLOCK_RE.findall(css):
            for name, val in CUSTOM_PROP_RE.findall(body):
                self.css_vars.setdefault(name, clean_text(val))

        # @import 1단계
        if depth < 1:
            for m in IMPORT_RE.finditer(css):
                imp = m.group(1) or m.group(2)
                iu = absolute(css_base, imp)
                if iu and iu not in seen:
                    seen.add(iu)
                    r = self.get(iu)
                    if r is not None and r.text:
                        self.stylesheets.append(iu)
                        self.parse_css(r.text, iu, depth + 1, seen)

    # ---- 페이지 ----
    def parse_page(self, html: str, page_url: str):
        base_url = page_url
        base_tag = re.search(r"<base\b([^>]*)>", html, re.I)
        if base_tag:
            b = parse_attrs(base_tag.group(1)).get("href")
            if b:
                base_url = absolute(page_url, b) or page_url

        title = TITLE_RE.search(html)
        lang_m = HTML_LANG_RE.search(html)
        info = {
            "url": page_url,
            "title": clean_text(title.group(1)) if title else "",
            "lang": parse_attrs(lang_m.group(1)).get("lang", "") if lang_m else "",
            "description": "",
            "og_image": "",
            "canonical": "",
        }

        for raw in META_RE.findall(html):
            a = parse_attrs(raw)
            key = (a.get("property") or a.get("name") or "").lower()
            content = a.get("content", "")
            if key in ("description", "og:description") and content:
                info["description"] = info["description"] or clean_text(content)
            elif key in ("og:image", "twitter:image") and content:
                u = absolute(base_url, content)
                if u:
                    info["og_image"] = u
                    self.add_asset(u, "image", "og:image", page_url)

        for raw in LINK_RE.findall(html):
            a = parse_attrs(raw)
            rel = (a.get("rel") or "").lower()
            href = absolute(base_url, a.get("href", ""))
            if not href:
                continue
            if "stylesheet" in rel:
                self.stylesheets.append(href)
                self.add_asset(href, "stylesheet", "link", page_url)
            elif "icon" in rel:
                self.add_asset(href, "icon", "favicon", page_url)
            elif rel == "canonical":
                info["canonical"] = href
            elif "preload" in rel:
                kind = a.get("as") or kind_of(href)
                self.add_asset(href, kind, "preload", page_url)

        for raw in IMG_RE.findall(html):
            a = parse_attrs(raw)
            for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                u = absolute(base_url, a.get(attr, ""))
                if u:
                    self.add_asset(u, kind_of(u, "image"), f"img:{attr}", page_url)
            for cand in split_srcset(a.get("srcset", "")):
                u = absolute(base_url, cand)
                if u:
                    self.add_asset(u, kind_of(u, "image"), "img:srcset", page_url)
            for cand in split_srcset(a.get("data-srcset", "")):
                u = absolute(base_url, cand)
                if u:
                    self.add_asset(u, kind_of(u, "image"), "img:data-srcset", page_url)

        for raw in SOURCE_RE.findall(html):
            a = parse_attrs(raw)
            for cand in split_srcset(a.get("srcset", "")):
                u = absolute(base_url, cand)
                if u:
                    self.add_asset(u, kind_of(u, "image"), "source:srcset", page_url)
            u = absolute(base_url, a.get("src", ""))
            if u:
                self.add_asset(u, kind_of(u), "source:src", page_url)

        for raw in VIDEO_RE.findall(html):
            a = parse_attrs(raw)
            u = absolute(base_url, a.get("poster", ""))
            if u:
                self.add_asset(u, "image", "video:poster", page_url)

        for attrs, body in STYLE_BLOCK_RE.findall(html):
            self.parse_css(body, base_url)

        info["inline_svg_count"] = len(SVG_RE.findall(html))
        info["html_bytes"] = len(html.encode("utf-8", "ignore"))
        info["sections"] = self.sniff_sections(html)
        return info, base_url

    def sniff_sections(self, html: str) -> list:
        """섹션 힌트: <section>, id/class에 section/hero/features/pricing 류"""
        hints = re.findall(r"<section\b[^>]*?(?:id|class)\s*=\s*[\"']([^\"']{1,80})[\"']", html, re.I)
        seen, out = set(), []
        for h in hints:
            for tok in re.split(r"\s+", h):
                if tok and tok not in seen:
                    seen.add(tok)
                    out.append(tok)
        return out[:40]

    def discover_sitemap(self, root: str):
        found = []
        for name in ("sitemap.xml", "sitemap_index.xml", "robots.txt"):
            u = urljoin(root, "/" + name)
            r = self.get(u)
            if r is None:
                continue
            found.append(u)
            if name == "robots.txt":
                for line in r.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        sm = line.split(":", 1)[1].strip()
                        self.extra_sitemaps.append(sm)
            elif "<sitemapindex" in r.text:
                for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text):
                    self.extra_sitemaps.append(loc)
            else:
                locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
                found.extend(locs[:50])
        return found

    def run(self):
        os.makedirs(self.args.out, exist_ok=True)

        for url in self.args.urls:
            r = self.get(url)
            if r is None:
                print(f"[FAIL] {url}")
                continue
            final = r.url
            info, base_url = self.parse_page(r.text, final)
            info["final_url"] = final
            info["status"] = r.status_code
            self.pages.append(info)
            print(f"[OK] {url} -> {final} ({len(r.text):,} bytes)")

            # 스타일시트 파싱(CSS 파일에서 폰트/이미지/변수 수집)
            for sheet in list(self.stylesheets):
                if sheet.startswith(("http://", "https://")) and sheet not in self._css_done:
                    self._css_done.add(sheet)
                    cr = self.get(sheet)
                    if cr is not None and cr.text:
                        self.parse_css(cr.text, sheet, depth=0, seen={sheet})
            if self.args.save_css:
                cssdir = os.path.join(self.args.out, "css")
                os.makedirs(cssdir, exist_ok=True)
                for sheet in self.stylesheets:
                    cr = self.get(sheet)
                    if cr is None:
                        continue
                    name = re.sub(r"[^\w.-]", "_", os.path.basename(urlsplit(sheet).path)) or "index.css"
                    with open(os.path.join(cssdir, name), "w", encoding="utf-8", errors="ignore") as f:
                        f.write(cr.text)

        if self.args.sitemap and self.pages:
            root = "{0.scheme}://{0.netloc}".format(urlsplit(self.pages[0]["final_url"]))
            self.discover_sitemap(root)

        # 페이지별 소유 표기
        first = self.pages[0]["final_url"] if self.pages else ""
        for rec in self.asset_index.values():
            rec["page"] = rec["page"] or first

        manifest = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": self.args.urls,
            "pages": self.pages,
            "stylesheets": sorted(set(self.stylesheets)),
            "fonts": self.fonts,
            "css_variables": self.css_vars,
            "assets": sorted(self.asset_index.values(), key=lambda r: r["url"]),
            "failed_requests": self.failed,
            "sitemap_candidates": self.extra_sitemaps,
            "stats": {
                "pages": len(self.pages),
                "assets": len(self.asset_index),
                "fonts": len(self.fonts),
                "stylesheets": len(set(self.stylesheets)),
                "css_variables": len(self.css_vars),
                "failed": len(self.failed),
            },
        }

        mpath = os.path.join(self.args.out, "manifest.json")
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        # 원본 HTML 저장
        if self.args.raw_html and self.pages:
            import requests as _rq  # noqa: F401
            r = self.get(self.pages[0]["final_url"])
            if r is not None:
                with open(os.path.join(self.args.out, "page.html"), "w", encoding="utf-8", errors="ignore") as f:
                    f.write(r.text)

        by_kind = {}
        for rec in self.asset_index.values():
            by_kind[rec["kind"]] = by_kind.get(rec["kind"], 0) + 1

        lines = [
            f"# probe summary — {', '.join(self.args.urls)}",
            f"pages: {len(self.pages)}",
            f"assets: {len(self.asset_index)}  ({', '.join(f'{k}={v}' for k, v in sorted(by_kind.items()))})",
            f"fonts: {len(self.fonts)}  css_vars: {len(self.css_vars)}  stylesheets: {len(set(self.stylesheets))}",
            f"failed: {len(self.failed)}",
            "",
            "## pages",
        ]
        for p in self.pages:
            lines.append(f"- {p['url']}  title={p['title'][:70]!r}  lang={p['lang']}  svg={p['inline_svg_count']}")
            if p["sections"]:
                lines.append(f"  sections: {', '.join(p['sections'][:18])}")
        if self.fonts:
            lines += ["", "## fonts"]
            for fo in self.fonts[:30]:
                lines.append(f"- {fo['family']!r} w={fo['weight']} s={fo['style']} -> {fo['url']}")
        if self.css_vars:
            lines += ["", "## css variables (first 40)"]
            for k, v in list(self.css_vars.items())[:40]:
                lines.append(f"- {k}: {v}")
        if self.failed:
            lines += ["", "## failed requests"]
            for f_ in self.failed[:40]:
                lines.append(f"- {f_['url']} ({f_['error']})")
        lines += ["", f"manifest: {mpath}"]

        spath = os.path.join(self.args.out, "summary.txt")
        with open(spath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("\n".join(lines))

    _css_done = None

    def __post_init__(self):
        self._css_done = set()


def main():
    ap = argparse.ArgumentParser(description="웹페이지 정적 1차 수집기 (manifest.json 생성)")
    ap.add_argument("urls", nargs="+", help="수집할 URL(여러 개 가능)")
    ap.add_argument("--out", required=True, help="산출 폴더 (예: docs/research/<site>)")
    ap.add_argument("--ua", default=DEFAULT_UA, help="User-Agent")
    ap.add_argument("--header", action="append", help="추가 헤더 'Key: Value' (반복 가능)")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--sitemap", action="store_true", help="robots.txt/sitemap.xml 탐색")
    ap.add_argument("--save-css", action="store_true", help="스타일시트 원본도 <out>/css/에 저장")
    ap.add_argument("--no-raw-html", dest="raw_html", action="store_false", help="page.html 저장 안 함")
    ap.set_defaults(raw_html=True)
    args = ap.parse_args()

    p = Probe(args)
    p.run()


if __name__ == "__main__":
    main()
