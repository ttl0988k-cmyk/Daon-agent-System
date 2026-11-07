"""REA 산출물 요약기 — 방대한 Evidence JSON을 구조 요약으로 압축한다.

REA의 inspect-web-page / analyze-web-bundle / capture-web-screenshot 결과는
수십만 자가 될 수 있어 그대로 컨텍스트에 넣으면 터진다. 이 스크립트는
키 구조·핵심 목록만 뽑고, 스크린샷은 PNG로 복원한다.

사용:
  python rea_summarize.py bundle  C:/daon/_rea_bundle.json
  python rea_summarize.py shot    C:/daon/_rea_shot.json  C:/daon/output/out.png
  python rea_summarize.py capture C:/daon/_rea_cap.json
"""
import base64
import json
import sys
from pathlib import Path


def load_normalized(path: str) -> dict:
    """Evidence 봉투면 normalized_result를, 아니면 원본을 돌려준다."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload.get("normalized_result", payload)


def describe(value) -> str:
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"dict{list(value.keys())[:14]}"
    return str(value)[:140]


def find_key(node, key):
    """중첩 구조에서 특정 키를 재귀 탐색."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for child in node.values():
            found = find_key(child, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_key(item, key)
            if found is not None:
                return found
    return None


def summarize(obj: dict, title: str) -> None:
    print(f"=== {title} ===")
    for key, value in obj.items():
        print(f"  {key}: {describe(value)}")


def cmd_bundle(path: str) -> None:
    bundle = load_normalized(path)
    summarize(bundle, "BUNDLE")
    for probe in ("vendor_fingerprints", "endpoint_candidates", "routes",
                  "endpoints", "source_maps", "chunks", "completeness",
                  "unknowns", "limitations"):
        hit = find_key(bundle, probe)
        if hit is not None:
            print(f"\n[{probe}] -> {describe(hit)}")
            print(json.dumps(hit, ensure_ascii=False)[:900])


def cmd_shot(path: str, out_png: str | None) -> None:
    shot = load_normalized(path)
    summarize(shot, "SCREENSHOT")
    print("  viewport:", shot.get("viewport"))
    data_b64 = find_key(shot, "data_base64")
    if data_b64 and out_png:
        target = Path(out_png)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(data_b64))
        print(f"PNG saved -> {target} ({target.stat().st_size} bytes)")
    elif not data_b64:
        print("data_base64 없음 — 스크린샷 캡처 실패")


def cmd_capture(path: str) -> None:
    cap = load_normalized(path)
    summarize(cap, "PAGE CAPTURE")
    for probe in ("completeness", "dom", "frames", "storage", "excluded"):
        hit = find_key(cap, probe)
        if hit is not None:
            print(f"\n[{probe}] -> {describe(hit)}")
            print(json.dumps(hit, ensure_ascii=False)[:700])


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    mode, path = sys.argv[1], sys.argv[2]
    if mode == "bundle":
        cmd_bundle(path)
    elif mode == "shot":
        cmd_shot(path, sys.argv[3] if len(sys.argv) > 3 else None)
    elif mode == "capture":
        cmd_capture(path)
    else:
        print(f"알 수 없는 모드: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
