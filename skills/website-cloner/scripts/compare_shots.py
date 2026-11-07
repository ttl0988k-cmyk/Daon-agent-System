#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compare_shots.py — 소스↔로컬 스크린샷 비교 (website-cloner 스킬)

같은 뷰포트·스크롤 위치·상태에서 찍은 두 이미지를 비교해:
  · 좌:소스 | 중:로컬 | 우:차이 히트맵(빨강) 3분할 몽타주 생성
  · 평균 절대 오차(MAE, 0~255), 임계 초과 픽셀 비율, 차이 영역 바운딩 박스
  · 크기 불일치 보고(치수 차이는 지오메트리 드리프트의 1차 신호)
  · JSON 리포트 출력

픽셀 차이는 안티앨리어싱·폰트 힌팅·서브픽셀 때문에 0이 되지 않는다.
0을 목표로 하지 말고 '구조적 차이'(섹션 유무·폭·줄바꿈)를 먼저 없앤다.

사용:
  python compare_shots.py src_1440.png local_1440.png -o diff_1440.png
  python compare_shots.py a.png b.png -o out.png --threshold 20 --fit contain --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from PIL import Image, ImageChops, ImageDraw, ImageFont


def load_font(size=18):
    for p in (r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def fit_image(img: Image.Image, size: tuple, mode: str, bg=(245, 245, 245)) -> Image.Image:
    """size 캔버스에 이미지 맞춤. contain=비율유지+여백, cover=비율유지+크롭, stretch=왜곡."""
    tw, th = size
    if img.size == size:
        return img.convert("RGB")
    if mode == "stretch":
        return img.convert("RGB").resize(size, Image.LANCZOS)
    sw, sh = img.size
    scale = min(tw / sw, th / sh) if mode == "contain" else max(tw / sw, th / sh)
    nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    resized = img.convert("RGB").resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", size, bg)
    if mode == "contain":
        canvas.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
    else:  # cover → 중앙 크롭
        left, top = (nw - tw) // 2, (nh - th) // 2
        canvas.paste(resized.crop((left, top, left + tw, top + th)), (0, 0))
    return canvas


def analyze(a: Image.Image, b: Image.Image, threshold: int):
    """RGB 두 장 → (MAE, diff_ratio, bbox, mask)"""
    diff = ImageChops.difference(a, b).convert("L")
    hist = diff.histogram()
    total = a.size[0] * a.size[1]
    weighted = sum(i * c for i, c in enumerate(hist))
    mae = weighted / total if total else 0.0
    over = sum(c for i, c in enumerate(hist) if i > threshold)
    ratio = over / total if total else 0.0
    mask = diff.point(lambda v: 255 if v > threshold else 0)
    bbox = mask.getbbox()
    return mae, ratio, bbox, mask


def heat_overlay(base: Image.Image, mask: Image.Image, bbox=None) -> Image.Image:
    """로컬 이미지를 흐리게 만든 뒤 차이 영역만 빨강으로 칠한다."""
    dim = Image.blend(base.convert("RGB"), Image.new("RGB", base.size, (255, 255, 255)), 0.55)
    red = Image.new("RGB", base.size, (235, 40, 40))
    out = Image.composite(red, dim, mask)
    if bbox:
        d = ImageDraw.Draw(out)
        for w in range(3):
            d.rectangle([bbox[0] - w, bbox[1] - w, bbox[2] + w, bbox[3] + w], outline=(20, 120, 255))
    return out


def label_strip(width: int, height: int, text: str, color=(25, 25, 25)) -> Image.Image:
    strip = Image.new("RGB", (width, height), (255, 255, 255))
    d = ImageDraw.Draw(strip)
    d.text((8, max(2, height // 2 - 9)), text, font=load_font(max(12, height - 10)), fill=color)
    return strip


def main():
    ap = argparse.ArgumentParser(description="소스↔로컬 스크린샷 비교(몽타주+수치)")
    ap.add_argument("src", help="소스 스크린샷")
    ap.add_argument("local", help="로컬 스크린샷")
    ap.add_argument("-o", "--out", default="", help="출력 몽타주 PNG")
    ap.add_argument("--threshold", type=int, default=16, help="차이 판정 임계(0~255, 기본 16)")
    ap.add_argument("--fit", choices=["contain", "cover", "stretch"], default="contain",
                    help="크기 불일치 시 맞춤 방식")
    ap.add_argument("--crop", default="", help="비교 영역 'x,y,w,h'")
    ap.add_argument("--json", action="store_true", help="JSON 리포트 stdout 출력")
    ap.add_argument("--scale", type=float, default=1.0, help="출력 축소 배율(0.5=절반)")
    args = ap.parse_args()

    if not os.path.exists(args.src):
        raise SystemExit(f"파일 없음: {args.src}")
    if not os.path.exists(args.local):
        raise SystemExit(f"파일 없음: {args.local}")

    A = Image.open(args.src)
    B = Image.open(args.local)
    src_size, loc_size = A.size, B.size

    if args.crop:
        try:
            x, y, w, h = [int(v) for v in args.crop.split(",")]
            A = A.crop((x, y, x + w, y + h))
            B = B.crop((x, y, x + w, y + h))
        except Exception as e:
            raise SystemExit(f"--crop 형식 오류: {e}")

    canvas = A.size
    B_fit = fit_image(B, canvas, args.fit)
    A_rgb = A.convert("RGB")

    mae, ratio, bbox, mask = analyze(A_rgb, B_fit, args.threshold)
    heat = heat_overlay(B_fit, mask, bbox)

    # 판정
    if ratio < 0.02:
        verdict = "PASS (근접)"
    elif ratio < 0.08:
        verdict = "WARN (부분 불일치)"
    else:
        verdict = "FAIL (구조적 차이)"

    gaps = []
    if src_size != loc_size:
        gaps.append(f"size_mismatch {src_size} vs {loc_size} (fit={args.fit})")
    if bbox:
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        rel = (bw * bh) / (canvas[0] * canvas[1])
        if rel > 0.5:
            gaps.append("차이 영역이 화면 절반 이상 — 섹션 누락/지오메트리 드리프트 의심")
    if ratio > 0.08:
        gaps.append("우선순위: 지오메트리 → 누락 섹션 → 타이포/줄바꿈 → 에셋 크롭 → 간격/모션")

    # 몽타주
    if args.out:
        pad, bar = 8, 26
        w = int(canvas[0] * args.scale)
        hgt = int(canvas[1] * args.scale)
        tiles = [A_rgb, B_fit, heat]
        names = [f"SRC  {os.path.basename(args.src)} ({src_size[0]}x{src_size[1]})",
                 f"LOCAL {os.path.basename(args.local)} ({loc_size[0]}x{loc_size[1]})",
                 f"DIFF  ratio={ratio*100:.2f}%  MAE={mae:.2f}  {verdict}"]
        tiles = [t.resize((w, hgt), Image.LANCZOS) if args.scale != 1.0 else t for t in tiles]
        total_w = w * 3 + pad * 4
        total_h = hgt + bar + pad * 2
        out = Image.new("RGB", (total_w, total_h), (255, 255, 255))
        for i, (t, nm) in enumerate(zip(tiles, names)):
            x = pad + i * (w + pad)
            out.paste(label_strip(w, bar, nm), (x, pad))
            out.paste(t, (x, pad + bar))
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        out.save(args.out)
        print(f"montage: {args.out}")

    result = {
        "src": os.path.abspath(args.src), "local": os.path.abspath(args.local),
        "src_size": src_size, "local_size": loc_size,
        "threshold": args.threshold,
        "mae": round(mae, 3),
        "diff_pixel_ratio": round(ratio, 5),
        "diff_percent": round(ratio * 100, 3),
        "diff_bbox": bbox,
        "verdict": verdict,
        "gaps": gaps,
        "montage": os.path.abspath(args.out) if args.out else None,
    }

    if args.out:
        with open(os.path.splitext(args.out)[0] + "_report.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"src   : {src_size[0]}x{src_size[1]}  {args.src}")
        print(f"local : {loc_size[0]}x{loc_size[1]}  {args.local}")
        print(f"MAE   : {mae:.2f} / 255")
        print(f"diff  : {ratio*100:.2f}%  (> {args.threshold})")
        print(f"bbox  : {bbox}")
        print(f"판정  : {verdict}")
        for g in gaps:
            print(f"  · {g}")


if __name__ == "__main__":
    main()
