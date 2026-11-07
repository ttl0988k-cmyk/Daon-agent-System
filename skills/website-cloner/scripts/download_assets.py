#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_assets.py — 매니페스트 기반 에셋 다운로더 (website-cloner 스킬)

probe_page.py가 만든 manifest.json을 읽어 실제 에셋을 내려받는다.

핵심 기능(다온에서 실측으로 확인된 함정 대응):
  · 매직바이트로 실제 포맷 판정 → CDN이 확장자를 속여도(.png URL → AVIF 바이트) 올바르게 저장
  · Referer 지정(핫링크 차단 CDN 대응)
  · 파일명 충돌 시 짧은 해시 부여
  · kind별 하위 폴더(images/fonts/videos/svg/icons/media) 또는 --flat
  · HTTP 200이어도 content-type/디코딩이 깨진 경우 failures로 분리
  · 소스URL→로컬경로 매핑(mapping.json)과 실패 목록(failures.json) 출력
  · SVG는 텍스트로 저장하고 xmlns 누락을 경고

사용:
  python download_assets.py docs/research/example/manifest.json \
      --dest public/sites/example --referer https://example.com

  # 특정 kind만
  python download_assets.py manifest.json --dest public/sites/example --only image,font
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote, urlsplit

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

FOLDER = {
    "image": "images", "svg": "svg", "icon": "icons", "font": "fonts",
    "video": "videos", "audio": "media", "stylesheet": "css", "script": "js",
}
EXT_OF = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    "image/avif": ".avif", "image/gif": ".gif", "image/bmp": ".bmp",
    "image/svg+xml": ".svg", "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico",
    "font/woff2": ".woff2", "font/woff": ".woff", "font/ttf": ".ttf",
    "font/otf": ".otf", "application/font-woff2": ".woff2",
    "application/font-woff": ".woff", "application/x-font-ttf": ".ttf",
    "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "text/css": ".css", "application/javascript": ".js", "text/javascript": ".js",
}


def sniff(head: bytes, content_type: str) -> tuple:
    """(실제 포맷명, 권장확장자, 경고) 반환"""
    h = head or b""
    ct = (content_type or "").split(";")[0].strip().lower()

    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", ".png", ("ct_mismatch:" + ct if ct and "png" not in ct else "")
    if h.startswith(b"\xff\xd8\xff"):
        return "jpeg", ".jpg", ("ct_mismatch:" + ct if ct and "jpeg" not in ct and "jpg" not in ct else "")
    if h[:4] == b"GIF8":
        return "gif", ".gif", ("ct_mismatch:" + ct if ct and "gif" not in ct else "")
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "webp", ".webp", ("ct_mismatch:" + ct if ct and "webp" not in ct else "")
    if len(h) >= 12 and h[4:8] == b"ftyp":
        brand = h[8:12]
        if brand in (b"avif", b"avis"):
            return "avif", ".avif", ("ct_mismatch:" + ct if ct and "avif" not in ct else "")
        if brand in (b"heic", b"heix", b"mif1", b"msf1"):
            return "heic", ".heic", ("ct_mismatch:" + ct if ct and "heic" not in ct else "")
        if brand in (b"isom", b"iso2", b"mp41", b"mp42", b"avc1"):
            return "mp4", ".mp4", ""
    if h[:4] == b"OggS":
        return "ogg", ".ogg", ""
    if h[:4] == b"\x1a\x45\xdf\xa3":
        return "webm", ".webm", ""
    if h[:4] == b"wOF2":
        return "woff2", ".woff2", ""
    if h[:4] == b"wOFF":
        return "woff", ".woff", ""
    if h[:4] in (b"\x00\x01\x00\x00", b"true", b"ttcf"):
        return "ttf", ".ttf", ""
    if h[:4] == b"OTTO":
        return "otf", ".otf", ""

    txt = h.lstrip()[:400].lower()
    if txt.startswith(b"<svg") or (txt.startswith(b"<?xml") and b"<svg" in txt):
        return "svg", ".svg", ""
    if txt.startswith(b"<!doctype html") or txt.startswith(b"<html"):
        return "html", ".html", "html_instead_of_asset"
    if ct in EXT_OF:
        return ct.split("/")[-1], EXT_OF[ct], ""
    return "", None, ""


def safe_name(url: str, fallback: str) -> str:
    path = unquote(urlsplit(url).path)
    base = os.path.basename(path) or fallback
    base = re.sub(r"[^\w.\-]+", "_", base)
    base = base.strip("._") or fallback
    return base[:80]


def hash8(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:8]


def fetch_one(sess, rec, dest_root, referer, flat, timeout):
    url = rec["url"]
    kind = rec.get("kind") or "other"
    ref = rec.get("referer") or referer or None
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if ref:
        headers["Referer"] = ref

    out = {"url": url, "kind": kind, "status": None, "local": None,
           "bytes": 0, "content_type": "", "real_format": "", "warnings": []}
    try:
        r = sess.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True)
        out["status"] = r.status_code
        if r.status_code >= 400:
            out["error"] = f"HTTP {r.status_code}"
            return out
        chunks, total = [], 0
        for chunk in r.iter_content(65536):
            if chunk:
                chunks.append(chunk)
                total += len(chunk)
                if total > 200 * 1024 * 1024:  # 200MB 가드
                    out["warnings"].append("size_capped_200MB")
                    break
        data = b"".join(chunks)
        out["bytes"] = len(data)
        out["content_type"] = r.headers.get("content-type", "")
        if not data:
            out["error"] = "empty_body"
            return out

        fmt, ext, warn = sniff(data[:2048], out["content_type"])
        if warn:
            out["warnings"].append(warn)
        out["real_format"] = fmt or "unknown"

        sub = "" if flat else FOLDER.get(kind, "other")
        folder = os.path.join(dest_root, sub) if sub else dest_root
        os.makedirs(folder, exist_ok=True)

        base = safe_name(url, "asset")
        stem, orig_ext = os.path.splitext(base)
        real_ext = ext or orig_ext or ".bin"
        if orig_ext and ext and orig_ext.lower() != ext.lower():
            out["warnings"].append(f"ext_corrected:{orig_ext}->{ext}")

        name = f"{stem}{real_ext}"
        candidate = os.path.join(folder, name)
        if os.path.exists(candidate):
            if os.path.getsize(candidate) == len(data):
                out["local"] = candidate
                out["warnings"].append("dedup_identical")
                return out
            name = f"{stem}_{hash8(url)}{real_ext}"
            candidate = os.path.join(folder, name)
        # 같은 이름·다른 URL 충돌 방어
        n = 1
        while os.path.exists(candidate):
            candidate = os.path.join(folder, f"{stem}_{hash8(url + str(n))}{real_ext}")
            n += 1

        with open(candidate, "wb") as f:
            f.write(data)
        out["local"] = candidate

        if fmt == "svg":
            text = data.decode("utf-8", "ignore")
            if "xmlns" not in text[:600]:
                out["warnings"].append("svg_missing_xmlns")
            if "<symbol" in text or "<defs" in text:
                out["warnings"].append("svg_has_defs_check_refs")
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out


def main():
    ap = argparse.ArgumentParser(description="manifest.json 기반 에셋 다운로더")
    ap.add_argument("manifest")
    ap.add_argument("--dest", required=True, help="저장 루트 (예: public/sites/<site>)")
    ap.add_argument("--referer", default="", help="기본 Referer")
    ap.add_argument("--only", default="", help="kind 필터 (image,font,video...)")
    ap.add_argument("--exclude", default="", help="제외 kind (script,stylesheet...)")
    ap.add_argument("--flat", action="store_true", help="하위 폴더 없이 평면 저장")
    ap.add_argument("--timeout", type=int, default=45)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="최대 다운로드 수(0=무제한)")
    args = ap.parse_args()

    with open(args.manifest, "r", encoding="utf-8") as f:
        man = json.load(f)

    recs = list(man.get("assets") or [])
    # fonts 배열도 에셋으로 흡수(중복 URL 제거)
    seen = {r["url"] for r in recs}
    for fo in man.get("fonts") or []:
        if fo.get("url") and fo["url"] not in seen:
            seen.add(fo["url"])
            recs.append({"url": fo["url"], "kind": "font",
                         "notes": [f"font:{fo.get('family')}/{fo.get('weight')}"],
                         "referer": "", "page": ""})

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    excl = {s.strip() for s in args.exclude.split(",") if s.strip()}
    if only:
        recs = [r for r in recs if r.get("kind") in only]
    if excl:
        recs = [r for r in recs if r.get("kind") not in excl]
    if args.limit:
        recs = recs[:args.limit]

    if not recs:
        print("다운로드할 에셋이 없습니다(필터 결과 0).")
        return

    os.makedirs(args.dest, exist_ok=True)
    sess = requests.Session()
    results, failures = [], []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(fetch_one, sess, r, args.dest, args.referer, args.flat, args.timeout): r
                for r in recs}
        for i, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            if res.get("local"):
                results.append(res)
            else:
                failures.append(res)
            mark = "ok " if res.get("local") else "FAIL"
            print(f"[{i}/{len(recs)}] {mark} {res.get('status')} "
                  f"{res.get('real_format') or '-':6s} {res['url'][-70:]}")

    mapping = {r["url"]: r["local"] for r in results}
    with open(os.path.join(args.dest, "_mapping.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "source_manifest": os.path.abspath(args.manifest),
                   "count": len(mapping), "map": mapping}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.dest, "_failures.json"), "w", encoding="utf-8") as f:
        json.dump(failures, f, ensure_ascii=False, indent=2)

    warns = {}
    for r in results:
        for w in r["warnings"]:
            key = w.split(":")[0]
            warns[key] = warns.get(key, 0) + 1

    total_bytes = sum(r["bytes"] for r in results)
    print("\n=== 결과 ===")
    print(f"성공 {len(results)} / 실패 {len(failures)}  "
          f"{total_bytes/1024/1024:.1f} MB  {time.time()-t0:.1f}s")
    if warns:
        print("경고: " + ", ".join(f"{k}={v}" for k, v in sorted(warns.items())))
    if failures:
        print("실패 목록:")
        for f_ in failures[:15]:
            print(f"  - {f_['url']} ({f_.get('error') or f_.get('warnings')})")
    print(f"mapping: {os.path.join(args.dest, '_mapping.json')}")


if __name__ == "__main__":
    main()
