# scroll-world

스크롤하면 카메라가 아이소메트릭 디오라마 장면 밖에서 안으로 날아들고, 다음 장면으로 **컷 없이** 연속 비행하는 랜딩 페이지를 생성합니다.

Apple 제품 페이지의 스크롤 애니메이션과 동일한 기법 — 카메라가 실제로 움직이고, 스크롤은 시간만 구동합니다.

---

## 핵심 원칙

> **심(seam)은 프레임 단위 동일해야 한다.**
> 커넥터 클립의 첫 프레임 = 이전 장면의 마지막 프레임, 끝 프레임 = 다음 장면의 첫 프레임.
> 이 규칙을 어기면 장면 전환 시 "팝"이 발생합니다.

---

## 사용 도구 (DAON 내장)

| 도구 | 용도 |
|------|------|
| `image_generate` | 장면 스틸 생성 (아이소메트릭 디오라마) |
| `video_generate` | 카메라 비행 클립 생성 (start+end frame 지정) |
| `video_extract_frames` | 클립의 첫/마지막 프레임 추출 (심 검증용) |
| `terminal` | ffmpeg 인코딩, 파일 관리 |
| `write_file` | 최종 HTML/JS/CSS 조립 |

외부 CLI 불필요. FAL_KEY 환경변수만 있으면 시스템 안에서完결.

---

## Step 0 — 사전 확인

1. **FAL_KEY** 확인: `echo %FAL_KEY%` (Windows) 또는 `echo $FAL_KEY`
2. **ffmpeg** 확인: `ffmpeg -version`
3. 사용자에게 비용 안내:
   - N개 장면 = N 이미지 생성 + N 다이브인 클립 + (N-1) 커넥터 클립
   - 총 비디오 생성: 2N-1개
   - 예상 비용: 모델에 따라 클립당 $0.15~$0.70

---

## Step 1 — 사용자 인터뷰

아래 정보를 수집합니다 (한 번에 묻지 말고 자연스럽게):

1. **주제** — "이 월드는 무엇에 대한 건가요? 사업, 제품, 아이디어 아무거나."
2. **브랜드킷** — 색상 팔레트, 브랜드명, 톤 (없으면 제안)
3. **아트 디렉션** — 아이소메트릭 디오라마(기본) / 실사 / 미니멀 / 기타
4. **장면 목록** — 카메라가 방문할 순서 (3~7개 권장)
5. **모바일 버전** — 세로 9:16 별도 체인 생성 여부
6. **예산 확인** — 예상 비용 제시 후 승인

---

## Step 2 — 장면 스틸 생성

각 장면마다 `image_generate`로 스틸을 생성합니다.

### 프롬프트 템플릿

```
Isometric diorama of {scene_description}, miniature tilt-shift style,
soft studio lighting, clean edges, {brand_colors} color palette,
floating on {background}, highly detailed, no text, no people,
architectural visualization, 3D render quality
```

### 파라미터
- aspect_ratio: "landscape" (16:9)
- 결과: scene_01.png, scene_02.png, ... scene_N.png

### 주의
- 모든 장면에 **동일한 스타일 프리앰블**을 사용하여 일관성 유지
- 브랜드 색상을 매 프롬프트에 포함
- "no text, no people" 반드시 포함 (모듈 감지 회피)

---

## Step 3 — 다이브인 클립 생성

각 장면의 스틸을 시작 프레임으로 하여, 카메라가 장면 안으로 들어가는 클립을 생성합니다.

### 도구 호출

```
video_generate(
    prompt="camera slowly pushes forward into the diorama scene, gentle parallax, cinematic depth of field, smooth dolly movement",
    image_url="{scene_still_url}",
    duration=5
)
```

### 결과
- dive_01.mp4, dive_02.mp4, ... dive_N.mp4
- 각 클립의 첫 프레임 = 해당 장면 스틸과 동일 (frame-lock)

---

## Step 4 — 프레임 추출 (심 준비)

각 다이브인 클립의 **마지막 프레임**을 추출합니다.

```
video_extract_frames(
    video_url="{dive_clip_url}",
    fps=30,
    output_dir="frames/dive_01"
)
```

추출된 `last_frame`이 다음 커넥터의 `image_url`(시작 프레임)로 사용됩니다.

마찬가지로 다음 장면 스틸을 커넥터의 `end_image_url`로 사용합니다.

---

## Step 5 — 커넥터 클립 생성 (핵심!)

연속하는 장면 사이를 연결하는 클립을 생성합니다.

### 도구 호출

```
video_generate(
    prompt="camera pulls back and pans right, transitioning from one diorama scene to the next, smooth aerial movement, continuous flight",
    image_url="{dive_01_last_frame_url}",
    end_image_url="{scene_02_still_url}",
    duration=5
)
```

### ⚠️ 심 규칙
- `image_url` = 이전 다이브인 클립의 **마지막 프레임** (Step 4에서 추출)
- `end_image_url` = 다음 장면의 **스틸** (Step 2에서 생성)
- **반드시 frame-lock 지원 모델 사용** (Kling 2.5, Luma, Seedance)
- frame-lock 미지원 모델(Wan, MiniMax)은 커넥터에 사용 금지

### 결과
- connector_01.mp4, connector_02.mp4, ... connector_(N-1).mp4

---

## Step 6 — 체인 순서 정의

최종 재생 순서:

```
dive_01 → connector_01 → dive_02 → connector_02 → dive_03 → ... → dive_N
```

총 클립 수: N + (N-1) = 2N-1

---

## Step 7 — 스크럽 엔진 조립

아래 구조로 최종 HTML을 작성합니다:

### HTML 구조

```html
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>{brand_name} — Scroll World</title>
    <style>
        /* 스크럽 엔진 CSS */
    </style>
</head>
<body>
    <div id="scroll-world"></div>
    <script>
        /* 스크럽 엔진 JS (아래 참조) */
    </script>
</body>
</html>
```

### 스크럽 엔진 핵심 로직 (바닐라 JS)

```javascript
const CONFIG = {
    clips: [
        { src: "dive_01.mp4", type: "dive" },
        { src: "connector_01.mp4", type: "connector" },
        { src: "dive_02.mp4", type: "dive" },
        // ...
    ],
    crossfadeDuration: 0.3,  // 심 크로스페이드 (초)
    scrollHeight: 500,       // vh (스크롤 총 길이)
};

// 1. 페이지 높이를 scrollHeight vh로 설정
// 2. 고정 viewport에 <video> 요소 생성
// 3. scroll 이벤트 → 진행률 계산 → 현재 클립 + currentTime 설정
// 4. 클립 경계에서 크로스페이드
// 5. 모바일: 터치 시 video priming (iOS Safari 대응)
```

### 스크롤→시간 매핑

```javascript
function onScroll() {
    const progress = window.scrollY / (document.body.scrollHeight - window.innerHeight);
    const totalDuration = clips.reduce((sum, c) => sum + c.duration, 0);
    const targetTime = progress * totalDuration;
    
    // 현재 클립 찾기
    let elapsed = 0;
    for (let i = 0; i < clips.length; i++) {
        if (elapsed + clips[i].duration > targetTime) {
            const localTime = targetTime - elapsed;
            clips[i].video.currentTime = localTime;
            break;
        }
        elapsed += clips[i].duration;
    }
}
```

### 심 크로스페이드

```javascript
// 클립 경계 ±crossfadeDuration 범위에서 두 클립을 겹침
if (localTime > clip.duration - crossfadeDuration) {
    const fadeProgress = (localTime - (clip.duration - crossfadeDuration)) / crossfadeDuration;
    currentVideo.style.opacity = 1 - fadeProgress;
    nextVideo.style.opacity = fadeProgress;
    nextVideo.currentTime = 0;
}
```

---

## Step 8 — 모바일 대응 (선택)

모바일 버전 요청 시:
- 모든 클립을 9:16 세로로 재렌더 (별도 프롬프트에 "vertical composition, portrait" 추가)
- 720p, GOP 4로 인코딩 (모바일 디코더 부하 감소)
- `clipMobile` / `connectorsMobile` 배열을 config에 추가
- 엔진이 `window.innerWidth < 768` 시 모바일 체인 자동 전환

---

## Step 9 — 최종 검증

- [ ] 모든 심에서 "팝" 없음 (크로스페이드 + frame-lock)
- [ ] 스크롤 반응성 (60fps, seek 폭주 없음)
- [ ] iOS Safari에서 검은 화면 없음 (playsinline, muted, priming)
- [ ] 모바일에서 세로 클립 서빙 (videoWidth < videoHeight 확인)
- [ ] Reduced motion 설정 시 정적 이미지 폴백

---

## 트러블슈팅

| 문제 | 원인 | 해결 |
|------|------|------|
| 심에서 "팝" | frame-lock 미지원 모델 사용 | Kling/Luma/Seedance로 교체 |
| iOS 검은 화면 | muted video 미재생 시 프레임 미렌더 | poster 유지 + touch priming |
| 모바일 버벅임 | 1080p 디코딩 과부하 | 720p + GOP 4 인코딩 |
| NSFW 필터 차단 | 실내/수영장 등 트리거 단어 | "empty, architectural, no people" 추가 |
| 스크롤 점프 | URL바 show/hide resize | width 변경만 감지하도록 게이트 |

---

## 비용 참고 (FAL.ai 기준)

| 모델 | 용도 | 클립당 비용 |
|------|------|------------|
| FLUX 2 Klein 9B | 장면 스틸 | ~$0.006 |
| Kling 2.5 Turbo Pro | 다이브인/커넥터 (frame-lock) | ~$0.35-0.70 |
| Luma Dream Machine | 커넥터 대안 | ~$0.25 |
| Seedance 2.0 | 커넥터 대안 | ~$0.30 |

5개 장면 기준: 5 스틸 + 9 비디오 ≈ **$3.5~$6.5**
