# -*- coding: utf-8 -*-
"""기억 자동화 게이트 검증 (배포판 토큰 누수 방지).

검증 항목:
  1) env DAON_MEMORY_AUTO_EXTRACT=off  -> memory_auto_enabled() == False
  2) env DAON_MEMORY_AUTO_EXTRACT=on   -> memory_auto_enabled() == True
  3) 기본 'auto' + OmniRoute 미가동      -> memory_auto_enabled() == False (프로브 없이 단락)
  4) 재시작 폭주 수정: _last_daily_ts / _last_maintenance_ts 가 0.0 이 아님
  5) daily 정제 facts 상한 == 200

scripts/ 는 배포 번들에 포함되지 않으므로 이 파일은 배포물에 영향이 없다.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, 'api'), os.path.join(ROOT, 'api', 'api')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import api.memory_store as m  # noqa: E402

results = []


def _check(label, cond, extra=''):
    results.append((label, bool(cond), extra))
    print('[{0}] {1} {2}'.format('PASS' if cond else 'FAIL', label, extra))


# 1) off
os.environ['DAON_MEMORY_AUTO_EXTRACT'] = 'off'
m._omniroute_probe_cache['ts'] = 0.0
off_val = m.memory_auto_enabled()
_check("env=off -> False", off_val is False, '(got {0})'.format(off_val))

# 2) on
os.environ['DAON_MEMORY_AUTO_EXTRACT'] = 'on'
on_val = m.memory_auto_enabled()
_check("env=on  -> True", on_val is True, '(got {0})'.format(on_val))

# 3) auto + OmniRoute 미가동 (프로브가 실제로 호출되는지 확인)
os.environ.pop('DAON_MEMORY_AUTO_EXTRACT', None)
m._omniroute_probe_cache['ts'] = 0.0
probe_called = {'n': 0}
_real_probe = m._omniroute_available


def _spy_probe():
    probe_called['n'] += 1
    return False


m._omniroute_available = _spy_probe
try:
    auto_val = m.memory_auto_enabled()
finally:
    m._omniroute_available = _real_probe
_check("auto + 라우터 없음 -> False", auto_val is False, '(got {0})'.format(auto_val))
_check("auto 경로가 프로브 호출", probe_called['n'] == 1)

# 실제 로컬 프로브 1회 (정보용)
try:
    print('    [info] 실제 OmniRoute 감지 결과 =', _real_probe())
except Exception as e:
    print('    [info] 프로브 예외:', e)

# 4) 재시작 폭주 수정
_check("_last_daily_ts != 0.0", m._last_daily_ts != 0.0, '(={0})'.format(m._last_daily_ts))
_check("_last_maintenance_ts != 0.0", m._last_maintenance_ts != 0.0,
       '(={0})'.format(m._last_maintenance_ts))

# 5) facts 상한
_check("_DAILY_REFINE_MAX_FACTS == 200", m._DAILY_REFINE_MAX_FACTS == 200,
       '(={0})'.format(m._DAILY_REFINE_MAX_FACTS))

failed = [r for r in results if not r[1]]
print('\nSUMMARY total={0} pass={1} fail={2}'.format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
