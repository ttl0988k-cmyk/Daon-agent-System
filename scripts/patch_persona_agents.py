import os
from pathlib import Path

index_path = Path(r"c:\daon\Daon agent System\index.html")
api_js_path = Path(r"c:\daon\Daon agent System\static\v2\js\api.js")
app_js_path = Path(r"c:\daon\Daon agent System\static\v2\js\app.js")

# 1. Update index.html
with open(index_path, "r", encoding="utf-8") as f:
    index_html = f.read()

index_html = index_html.replace(
    '<option value="빌(개발)">💻 빌 (풀스택·백엔드·API 구현)</option>',
    '<option value="빌(개발)">🔨 빌 (풀스택·백엔드·API 구현)</option>'
)
index_html = index_html.replace('app.js?v=20261008_2315', 'app.js?v=20261008_2340')

with open(index_path, "w", encoding="utf-8") as f:
    f.write(index_html)
print("index.html patched.")

# 2. Update api.js
with open(api_js_path, "r", encoding="utf-8") as f:
    api_js = f.read()

# Make createSession support profile parameter
old_create_session = """  async createSession(title = '새 세션') {
    const res = await fetch(`${this.baseUrl}/api/session/new`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title })
    });
    if (!res.ok) throw new Error(`Failed to create session: ${res.status}`);
    return await res.json();
  },"""

new_create_session = """  async createSession(title = '새 세션', profile = null) {
    const payload = { title };
    if (profile) payload.profile = profile;
    const res = await fetch(`${this.baseUrl}/api/session/new`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error(`Failed to create session: ${res.status}`);
    return await res.json();
  },"""

if old_create_session in api_js:
    api_js = api_js.replace(old_create_session, new_create_session)
    print("api.js createSession patched.")
else:
    print("WARNING: api.js createSession not matched.")

# Filter out daon response profile in getProfiles and ensure switchProfile
old_profiles = """  /**
   * Agent Profiles
   */
  async getProfiles() {
    const res = await fetch(`${this.baseUrl}/api/profiles`);
    if (!res.ok) return { profiles: [], active: 'default' };
    return await res.json();
  },

  async switchProfile(name) {
    const res = await fetch(`${this.baseUrl}/api/profile/switch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    return res.ok;
  }"""

new_profiles = """  /**
   * Agent Profiles (정적 에이전트: 토니, 빌, 셜록, 프라다, 라온 / 다온응대 제외)
   */
  async getProfiles() {
    try {
      const res = await fetch(`${this.baseUrl}/api/profiles`);
      if (!res.ok) return { profiles: [], active: 'raon' };
      const data = await res.json();
      if (Array.isArray(data.profiles)) {
        // 대표님 요구사항: '다온응대'는 필요 없으므로 완전히 필터링
        data.profiles = data.profiles.filter(p => {
          const n = (p.name || '').toLowerCase();
          return !n.includes('다온') && !n.includes('daon');
        });
      }
      return data;
    } catch (_) {
      return { profiles: [], active: 'raon' };
    }
  },

  async switchProfile(name) {
    try {
      const res = await fetch(`${this.baseUrl}/api/profile/switch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      });
      return res.ok;
    } catch (e) {
      console.warn('switchProfile error:', e);
      return false;
    }
  },

  async createProfile(name) {
    const res = await fetch(`${this.baseUrl}/api/profile/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `프로필 생성 실패: ${res.status}`);
    }
    return await res.json();
  }"""

if old_profiles in api_js:
    api_js = api_js.replace(old_profiles, new_profiles)
    print("api.js profiles methods patched.")
else:
    print("WARNING: api.js profiles methods not matched.")

with open(api_js_path, "w", encoding="utf-8") as f:
    f.write(api_js)

print("api.js saved successfully.")
