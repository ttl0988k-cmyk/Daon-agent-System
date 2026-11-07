"""manifest.json 기준 sha256 대조기.

export-web-scripts가 남긴 manifest.json으로 추출본과 복원본이
바이트 단위로 일치하는지 검증한다. "복제했다"의 객관적 증거.

사용:
  python verify_hashes.py C:/daon/_rea_scripts/manifest.json C:/daon/_rea_rebuilt

주의: layout == "isolated"(인라인 스크립트)는 원본 HTML에 인라인된 형태라
파일 대조 대상이 아니다. 그건 복원 HTML에서 <script type="module"> 본문을
추출해 manifest의 sha256과 3자 대조한다.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ORIGIN_PREFIX = re.compile(r"^origins/[0-9a-f]+/(.+)$")


def origin_relative(relative_path: str) -> str | None:
    """origins/<hash>/<path> → <path>. isolated(인라인)면 None."""
    match = ORIGIN_PREFIX.match(relative_path or "")
    return match.group(1) if match else None


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    rebuilt = Path(sys.argv[2])

    matched = mismatch = skipped = 0
    for entry in manifest["scripts"]:
        content = entry["content"]
        relative = origin_relative(content.get("relative_path", ""))
        if relative is None:
            skipped += 1  # 인라인 스크립트 — 복원 HTML과 별도 대조
            continue
        target = rebuilt / relative
        if not target.exists():
            mismatch += 1
            print(f"  MISSING {relative}")
        elif sha256_of(target) == content["sha256"]:
            matched += 1
        else:
            mismatch += 1
            print(f"  DIFF    {relative}")

    print(f"\n일치 {matched} / 불일치 {mismatch} / 제외(인라인) {skipped}")
    sys.exit(0 if mismatch == 0 else 2)


if __name__ == "__main__":
    main()
