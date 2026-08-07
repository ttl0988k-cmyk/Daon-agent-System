"""run_dev_server.py — api.api 패키지를 sys.path 우회로 인식."""
import sys
import os
import importlib
from pathlib import Path

ROOT_API = Path(__file__).resolve().parent  # C:\daon\Daon agent System\api
ROOT_PROJECT = ROOT_API.parent

os.chdir(ROOT_API)

# 'api' 패키지: 프로젝트 루트가 sys.path 에 있으면 됨
sys.path.insert(0, str(ROOT_PROJECT))

# 'api.api' 서브패키지: importlib 으로 직접 로드 후 sys.modules 에 등록
# (api/api/__init__.py 가 있으므로 정상 패키지)
import api  # noqa: F401  # parent
print(f"[run_dev_server] api package: {api.__file__}")

import importlib.util
spec = importlib.util.spec_from_file_location(
    "api.api",
    ROOT_API / "api" / "__init__.py",
    submodule_search_locations=[str(ROOT_API / "api")],
)
api_pkg = importlib.util.module_from_spec(spec)
sys.modules["api.api"] = api_pkg
spec.loader.exec_module(api_pkg)
print(f"[run_dev_server] api.api injected: {api_pkg.__file__}")

# 이제 api.api.X 형태 import 가능
from api.api.agents.creative_director.dev_server import main
main()