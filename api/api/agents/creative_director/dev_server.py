"""
Creative Director 검증용 미니 서버 (포트 9092).

다온 본 서버(9090, 9091)는 절대 안 건드림.
9092에서 라우트 4개만 standalone 으로 띄워서 우리 수정사항이 동작하는지 확인.

주의: PyInstaller server.exe 처럼 메인 패키지 안에 바이너리 동봉하지 않으므로,
디스크의 변경사항을 그대로 읽는다 → Phase 1+2 의 라우트 패치가 즉시 반영됨.
"""

import sys
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ── sys.path 세팅 ──
ROOT = Path(__file__).resolve().parents[3]  # C:\daon\Daon agent System
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

# 'api.api' 가 import 안 되는 문제 우회: api/api/__init__.py 를 직접 inject
import importlib.util as _imp
_api_api_init = ROOT / "api" / "api" / "__init__.py"
if _api_api_init.exists():
    _spec = _imp.spec_from_file_location(
        "api.api",
        str(_api_api_init),
        submodule_search_locations=[str(ROOT / "api" / "api")],
    )
    _mod = _imp.module_from_spec(_spec)
    sys.modules["api.api"] = _mod
    _spec.loader.exec_module(_mod)

PORT = 9092


class Handler(BaseHTTPRequestHandler):
    """Creative Director 라우트 4개만 처리."""

    def log_message(self, fmt, *args):
        """콘솔에 접근 로그 출력."""
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            return self._send_json(200, {"ok": True, "service": "creative-director-dev", "port": PORT})
        if path == "/api/creative-director/health":
            return self._handle_health()
        if path == "/api/creative-director/cards":
            return self._handle_list_cards()
        return self._send_json(404, {"error": "Not found", "path": path})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except json.JSONDecodeError:
            return self._send_json(400, {"error": "Invalid JSON body"})

        if path == "/api/creative-director/brief":
            return self._handle_brief(body)
        if path == "/api/creative-director/cards/extract":
            return self._handle_extract(body)
        return self._send_json(404, {"error": "Not found", "path": path})

    # ── 핸들러 ──

    def _handle_health(self):
        try:
            from api.api.agents.creative_director import create_design_brief
            ok = callable(create_design_brief)
            from api.api.style_card import StyleCardRegistry
            reg = StyleCardRegistry()
            lib_root = ROOT / "data" / "reference_library"
            if lib_root.exists():
                reg.load_all(lib_root)
            return self._send_json(200, {
                "ok": ok,
                "phase": 2,
                "library_root": str(lib_root),
                "library_exists": lib_root.exists(),
                "card_count": reg.card_count,
                "version": "1.0-dev",
                "layers": ["ux_researcher", "design_librarian", "art_director", "creative_director"],
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._send_json(500, {"ok": False, "error": str(e)})

    def _handle_list_cards(self):
        try:
            from api.api.style_card import StyleCardRegistry
            reg = StyleCardRegistry()
            lib_root = ROOT / "data" / "reference_library"
            if lib_root.exists():
                reg.load_all(lib_root)

            cards = []
            for card_id, card in sorted(reg._cards.items()):
                eval_data = getattr(card, "evaluation", None)
                if hasattr(eval_data, "score"):
                    score = eval_data.score
                else:
                    score = 0
                cards.append({
                    "id": card.id,
                    "name": card.name,
                    "category": card.category,
                    "tags": getattr(card, "tags", []),
                    "source": getattr(card, "source", ""),
                    "score": score,
                    "components": len(getattr(card, "decomposed_cards", []) or []),
                })

            return self._send_json(200, {
                "cards": cards,
                "total": len(cards),
                "library_root": str(lib_root),
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._send_json(500, {"error": str(e)})

    def _handle_brief(self, body):
        try:
            user_mission = body.get("user_mission")
            if not user_mission:
                return self._send_json(400, {"error": "user_mission required"})

            from api.api.agents.creative_director import create_design_brief

            brief = create_design_brief(
                user_mission=user_mission,
                context=body.get("context") or {},
                library_root=Path(body["library_root"]) if body.get("library_root") else ROOT / "data" / "reference_library",
                n_candidates=int(body.get("n_candidates", 5)),
                llm_callable=None,
            )

            return self._send_json(200, {
                "markdown": brief.markdown,
                "spec": brief.spec,
                "creative_brief": brief.creative_brief,
                "candidates": brief.candidates,
                "mix": brief.mix,
                "warnings": brief.warnings,
                "scores": brief.scores,
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._send_json(500, {"error": str(e)})

    def _handle_extract(self, body):
        try:
            name = body.get("name")
            description = body.get("description")
            if not name or not description:
                return self._send_json(400, {"error": "name + description required"})

            from api.api.style_card import (
                StyleCard, DesignDNA, Composition, Guidelines, Evaluation,
            )
            import uuid
            from datetime import datetime, timezone

            category = body.get("category", "other")
            source = body.get("source", f"internal:{name.lower().replace(' ', '-')}")
            tags = body.get("tags", [])

            card = StyleCard(
                id=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
                name=name,
                source=source,
                category=category,
                created=datetime.now(timezone.utc).isoformat(),
                description=description,
                tags=tags,
                design_dna=DesignDNA(),
                composition=Composition(),
                guidelines=Guidelines(),
                evaluation=Evaluation(score=0.0, reviewed=False),
                decomposed_cards=[],
            )

            cards_dir = ROOT / "data" / "reference_library" / "cards"
            cards_dir.mkdir(parents=True, exist_ok=True)
            card.save(cards_dir)

            return self._send_json(200, {
                "ok": True,
                "card": card.to_dict(),
                "path": str(cards_dir / f"{card.id}.yaml"),
                "phase": 2,
                "note": "DNA 는 기본값. Phase 3 에서 StyleCardExtractor 가 자동 채움.",
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._send_json(500, {"error": str(e)})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"╔══════════════════════════════════════════════╗")
    print(f"║ Creative Director DEV Server (NOT production)║")
    print(f"║ Port: {PORT}  (9090 / 9091 / 9222 는 안 건드림) ║")
    print(f"║ Endpoints:                                    ║")
    print(f"║   GET  /api/creative-director/health          ║")
    print(f"║   GET  /api/creative-director/cards           ║")
    print(f"║   POST /api/creative-director/brief           ║")
    print(f"║   POST /api/creative-director/cards/extract   ║")
    print(f"╚══════════════════════════════════════════════╝")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dev_server] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()