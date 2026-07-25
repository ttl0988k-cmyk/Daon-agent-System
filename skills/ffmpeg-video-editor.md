---
name: ffmpeg-video-editor
version: "1.0"
category: media
priority: high
tags:
  - ffmpeg
  - video
  - audio
  - editing
  - encoding
  - subtitle
  - lut
  - transition
  - overlay
  - media
conflicts_with: []
graph_requires: []
graph_compatible:
  - full-output
  - self-reflection
  - creative-director
  - ui-ux-pro
graph_conflicts: []
purpose: "ffmpeg를 활용한 영상 편집·인코딩·합성 자동화. 자르기, 합치기, 자막, LUT 컬러그레이딩, 효과음/BGM 삽입, 오버레이, 트렌지션, 리사이즈, 포맷 변환 등 터미널에서 실행 가능한 모든 영상 작업을 다룬다."
when_to_use: "영상 자르기/합치기, 자막 삽입, BGM/효과음 추가, LUT 적용, 오버레이 합성, 포맷 변환, 리사이즈, 썸네일 추출, GIF 생성, 영상 분석(ffprobe), 일괄 처리"
when_not_to_use: "실시간 영상 편집 GUI 필요 시, 3D 렌더링, 복잡한 모션 그래픽(After Effects급), AI 영상 생성"
inputs: "소스 영상/오디오 파일 경로, 편집 요구사항 (자르기 구간, 자막 텍스트, BGM 파일, LUT 파일 등)"
outputs: "편집된 영상 파일, 추출된 프레임/오디오, 변환된 포맷, 분석 리포트"
examples: "영상 앞뒤 자르기, 여러 영상 합치기, SRT 자막 하드코딩, LUT 컬러그레이딩, BGM 볼륨 조절, 워터마크 오버레이, 4K→1080p 다운스케일, 영상→GIF"
constraints: "ffmpeg가 시스템에 설치되어 있어야 함. 복잡한 합성은 filter_complex 사용. 큰 파일은 처리 시간 고려. 원본 백업 권장."
success_criteria: "출력 영상이 정상 재생, 화질 열화 최소화, 오디오 싱크 일치, 자막 타이밍 정확, 의도한 효과 적용"
---

# FFmpeg Video Editor — 영상 편집 자동화 스킬

> 터미널에서 ffmpeg로 실행 가능한 모든 영상 편집 작업을 다룬다.
> 모든 명령은 **실제 실행 전 파일 존재를 확인**하고, **원본을 백업**한 후 실행한다.

---

## 0. 사전 확인 (작업 전 필수)

```bash
# ffmpeg 설치 확인
ffmpeg -version

# ffprobe로 소스 정보 확인
ffprobe -v quiet -print_format json -show_format -show_streams "input.mp4"
```

확인 항목:
- 코덱 (h264, hevc, vp9, av1)
- 해상도 (1920x1080 등)
- 프레임레이트 (24, 30, 60fps)
- 오디오 코덱/샘플레이트
- 영상 길이
- 파일 크기

---

## 1. 기본 작업

### 1.1 자르기 (Trim)

```bash
# 시간 기반 자르기 (00:01:30 ~ 00:03:00)
ffmpeg -i input.mp4 -ss 00:01:30 -to 00:03:00 -c copy output_trim.mp4

# 프레임 정확 자르기 (재인코딩)
ffmpeg -i input.mp4 -ss 00:01:30 -to 00:03:00 -c:v libx264 -c:a aac output_trim.mp4
```

> 💡 `-c copy`는 빠르지만 키프레임 단위로 잘림. 프레임 정확도가 필요하면 재인코딩.

### 1.2 합치기 (Concat)

```bash
# 같은 코덱/해상도: concat demuxer (빠름)
# filelist.txt:
#   file 'clip1.mp4'
#   file 'clip2.mp4'
#   file 'clip3.mp4'
ffmpeg -f concat -safe 0 -i filelist.txt -c copy output_merged.mp4

# 다른 코덱/해상도: concat filter (재인코딩)
ffmpeg -i clip1.mp4 -i clip2.mp4 -filter_complex \
  "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]" \
  -map "[outv]" -map "[outa]" output_merged.mp4
```

### 1.3 포맷 변환

```bash
# MP4 → WebM (VP9)
ffmpeg -i input.mp4 -c:v libvpx-vp9 -crf 30 -b:v 0 -c:a libopus output.webm

# MP4 → MOV (ProRes)
ffmpeg -i input.mp4 -c:v prores_ks -profile:v 3 -c:a pcm_s16le output.mov

# MOV/AVI → MP4 (H.264)
ffmpeg -i input.mov -c:v libx264 -preset medium -crf 23 -c:a aac -b:a 192k output.mp4

# 영상 → 오디오 추출
ffmpeg -i input.mp4 -vn -c:a libmp3lame -q:a 2 output.mp3
ffmpeg -i input.mp4 -vn -c:a aac -b:a 256k output.m4a
```

### 1.4 리사이즈 / 크롭

```bash
# 4K → 1080p
ffmpeg -i input_4k.mp4 -vf "scale=1920:1080" -c:v libx264 -crf 23 -c:a copy output_1080p.mp4

# 세로 영상 (9:16) — 중앙 크롭
ffmpeg -i input.mp4 -vf "crop=ih*9/16:ih" -c:v libx264 -crf 23 -c:a copy output_vertical.mp4

# 가로 영상 (16:9) — 레터박스
ffmpeg -i input.mp4 -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black" output.mp4

# 특정 영역 크롭 (x=100, y=50, w=640, h=480)
ffmpeg -i input.mp4 -vf "crop=640:480:100:50" output_crop.mp4
```

### 1.5 속도 조절

```bash
# 2배속 (영상 + 오디오)
ffmpeg -i input.mp4 -filter_complex "[0:v]setpts=0.5*PTS[v];[0:a]atempo=2.0[a]" -map "[v]" -map "[a]" output_2x.mp4

# 0.5배속 (슬로모션)
ffmpeg -i input.mp4 -filter_complex "[0:v]setpts=2.0*PTS[v];[0:a]atempo=0.5[a]" -map "[v]" -map "[a]" output_slow.mp4

# 4배속 (오디오는 2단계 atempo)
ffmpeg -i input.mp4 -filter_complex "[0:v]setpts=0.25*PTS[v];[0:a]atempo=2.0,atempo=2.0[a]" -map "[v]" -map "[a]" output_4x.mp4
```

---

## 2. 자막

### 2.1 SRT 하드코딩 (영상에 굽기)

```bash
# 기본 자막
ffmpeg -i input.mp4 -vf "subtitles=sub.srt" output.mp4

# 스타일 지정 (폰트, 크기, 색상, 위치)
ffmpeg -i input.mp4 -vf "subtitles=sub.srt:force_style='FontName=Malgun Gothic,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,MarginV=30'" output.mp4

# ASS/SSA 자막
ffmpeg -i input.mp4 -vf "ass=sub.ass" output.mp4
```

### 2.2 소프트 자막 (토글 가능)

```bash
# MP4에 자막 스트림 추가
ffmpeg -i input.mp4 -i sub.srt -c copy -c:s mov_text output_sub.mp4

# MKV에 자막 스트림 추가
ffmpeg -i input.mp4 -i sub.srt -c copy -c:s srt output_sub.mkv
```

### 2.3 텍스트 오버레이 (자막 없이 직접)

```bash
# 상단 중앙 텍스트
ffmpeg -i input.mp4 -vf "drawtext=text='DAON':fontfile='C\:/Windows/Fonts/malgun.ttf':fontsize=48:fontcolor=white:borderw=2:bordercolor=black:x=(w-text_w)/2:y=50" output.mp4

# 시간 제한 텍스트 (5초~10초)
ffmpeg -i input.mp4 -vf "drawtext=text='자막 텍스트':fontfile='C\:/Windows/Fonts/malgun.ttf':fontsize=36:fontcolor=white:borderw=2:x=(w-text_w)/2:y=h-80:enable='between(t,5,10)'" output.mp4
```

---

## 3. 오디오

### 3.1 BGM 추가 / 교체

```bash
# 기존 오디오 + BGM 믹싱 (BGM 볼륨 30%)
ffmpeg -i input.mp4 -i bgm.mp3 -filter_complex \
  "[0:a]volume=1.0[main];[1:a]volume=0.3[bgm];[main][bgm]amix=inputs=2:duration=first:dropout_transition=3[aout]" \
  -map 0:v -map "[aout]" -c:v copy output_bgm.mp4

# 기존 오디오 제거 + BGM 교체
ffmpeg -i input.mp4 -i bgm.mp3 -map 0:v -map 1:a -c:v copy -c:a aac -shortest output_newaudio.mp4

# BGM 페이드인/페이드아웃
ffmpeg -i bgm.mp3 -af "afade=t=in:st=0:d=2,afade=t=out:st=58:d=2" bgm_faded.mp3
```

### 3.2 효과음 삽입

```bash
# 특정 시간에 효과음 삽입 (3초 지점)
ffmpeg -i input.mp4 -i sfx.wav -filter_complex \
  "[1:a]adelay=3000|3000[sfx];[0:a][sfx]amix=inputs=2:duration=first[aout]" \
  -map 0:v -map "[aout]" -c:v copy output_sfx.mp4

# 여러 효과음 동시 삽입
ffmpeg -i input.mp4 -i sfx1.wav -i sfx2.wav -filter_complex \
  "[1:a]adelay=2000|2000[s1];[2:a]adelay=5000|5000[s2];[0:a][s1][s2]amix=inputs=3:duration=first[aout]" \
  -map 0:v -map "[aout]" -c:v copy output_sfx.mp4
```

### 3.3 볼륨 / 오디오 처리

```bash
# 볼륨 조절 (2배)
ffmpeg -i input.mp4 -af "volume=2.0" -c:v copy output_loud.mp4

# 오디오 정규화
ffmpeg -i input.mp4 -af "loudnorm=I=-16:TP=-1.5:LRA=11" -c:v copy output_normalized.mp4

# 오디오 페이드인/아웃
ffmpeg -i input.mp4 -af "afade=t=in:st=0:d=1,afade=t=out:st=59:d=1" -c:v copy output_fade.mp4

# 샘플레이트 변경
ffmpeg -i input.wav -ar 44100 output_44k.wav
```

---

## 4. LUT 컬러그레이딩

```bash
# .cube LUT 적용
ffmpeg -i input.mp4 -vf "lut3d=file='D:/참고용/LUTs/teal_orange.cube'" -c:a copy output_lut.mp4

# LUT + 밝기/대비 조절
ffmpeg -i input.mp4 -vf "lut3d=file='warm.cube',eq=brightness=0.05:contrast=1.1:saturation=1.2" output_graded.mp4

# LUT 강도 조절 (50% 블렌드)
ffmpeg -i input.mp4 -filter_complex \
  "[0:v]split[orig][lut];[lut]lut3d=file='cinematic.cube'[graded];[orig][graded]blend=all_mode=normal:all_opacity=0.5[out]" \
  -map "[out]" -map 0:a -c:a copy output_blend.mp4
```

> 💡 D:\참고용\LUTs 폴더의 .cube 파일을 활용 가능.

---

## 5. 오버레이 / 합성

### 5.1 워터마크 / 로고

```bash
# 우측 하단 로고 (여백 10px)
ffmpeg -i input.mp4 -i logo.png -filter_complex \
  "overlay=W-w-10:H-h-10" output_wm.mp4

# 반투명 워터마크 (50%)
ffmpeg -i input.mp4 -i logo.png -filter_complex \
  "[1:v]format=rgba,colorchannelmixer=aa=0.5[logo];[0:v][logo]overlay=W-w-10:H-h-10" output_wm.mp4

# 애니메이션 워터마크 (5초부터 표시)
ffmpeg -i input.mp4 -i logo.png -filter_complex \
  "overlay=W-w-10:H-h-10:enable='gte(t,5)'" output_wm.mp4
```

### 5.2 영상 오버레이 (PiP)

```bash
# Picture-in-Picture (우측 하단, 320x180)
ffmpeg -i main.mp4 -i sub.mp4 -filter_complex \
  "[1:v]scale=320:-1[pip];[0:v][pip]overlay=W-w-10:H-h-10" output_pip.mp4

# 오버레이 영상 (전체 화면, 반투명)
ffmpeg -i base.mp4 -i overlay.mp4 -filter_complex \
  "[1:v]format=rgba,colorchannelmixer=aa=0.7[ov];[0:v][ov]overlay=0:0" output_overlay.mp4
```

### 5.3 이미지 오버레이

```bash
# 특정 시간에 이미지 표시 (3초~8초)
ffmpeg -i input.mp4 -i sticker.png -filter_complex \
  "[1:v]format=rgba[img];[0:v][img]overlay=100:100:enable='between(t,3,8)'" output_sticker.mp4
```

> 💡 D:\참고용\오버레이, D:\참고용\움직이는이모지 폴더의 에셋 활용 가능.

---

## 6. 트렌지션 (전환 효과)

```bash
# xfade: 두 영상 간 전환 (1초 페이드, 5초 지점)
ffmpeg -i clip1.mp4 -i clip2.mp4 -filter_complex \
  "[0:v][1:v]xfade=transition=fade:duration=1:offset=5[outv];[0:a][1:a]acrossfade=d=1[outa]" \
  -map "[outv]" -map "[outa]" output_transition.mp4

# 사용 가능한 트렌지션:
# fade, wipeleft, wiperight, wipeup, wipedown,
# slideleft, slideright, slideup, slidedown,
# circlecrop, rectcrop, distance, fadeblack, fadewhite,
# radial, smoothleft, smoothright, smoothup, smoothdown,
# circleopen, circleclose, vertopen, vertclose,
# horzopen, horzclose, dissolve, pixelize, diagtl, diagtr,
# diagbl, diagbr, hlslice, hrslice, vuslice, vdslice,
# hblur, fadegrays, wipetl, wipetr, wipebl, wipebr,
# squeezeh, squeezev, zoomin, hlwind, hrwind, vuwind, vdwind,
# coverleft, coverright, coverup, coverdown,
# revealleft, revealright, revealup, revealdown
```

> 💡 D:\참고용\트렌지션 폴더의 영상 오버레이를 `overlay` + `enable`으로 합성 가능.

---

## 7. 프레임 / 썸네일 / GIF

### 7.1 프레임 추출

```bash
# 1초마다 프레임 추출
ffmpeg -i input.mp4 -vf "fps=1" frames/frame_%04d.png

# 특정 시간 프레임 (00:02:30)
ffmpeg -i input.mp4 -ss 00:02:30 -frames:v 1 thumbnail.png

# 장면 전환 프레임만 추출
ffmpeg -i input.mp4 -vf "select='gt(scene,0.3)'" -vsync vfr scene_%04d.png
```

### 7.2 썸네일 시트

```bash
# 10x10 썸네일 그리드
ffmpeg -i input.mp4 -vf "fps=1/10,scale=160:-1,tile=10x10" thumbnail_sheet.png
```

### 7.3 GIF 생성

```bash
# 고품질 GIF (팔레트 최적화)
ffmpeg -i input.mp4 -ss 00:00:05 -t 3 -vf "fps=15,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" output.gif

# 루프 GIF
ffmpeg -i input.mp4 -vf "fps=15,scale=320:-1,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" -loop 0 output_loop.gif
```

---

## 8. 영상 분석 (ffprobe)

```bash
# 전체 정보 (JSON)
ffprobe -v quiet -print_format json -show_format -show_streams "input.mp4"

# 영상 길이만
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "input.mp4"

# 해상도만
ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "input.mp4"

# 코덱 정보
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,profile,bit_rate -of default=noprint_wrappers=1 "input.mp4"

# 비트레이트 / 파일 크기
ffprobe -v error -show_entries format=bit_rate,size -of default=noprint_wrappers=1 "input.mp4"
```

---

## 9. 일괄 처리

```bash
# 폴더 내 모든 MP4 → 1080p 변환 (Windows)
for %f in (*.mp4) do ffmpeg -i "%f" -vf "scale=-2:1080" -c:v libx264 -crf 23 -c:a copy "converted\%~nf_1080p.mp4"

# 폴더 내 모든 영상에 워터마크
for %f in (*.mp4) do ffmpeg -i "%f" -i logo.png -filter_complex "overlay=W-w-10:H-h-10" -c:a copy "watermarked\%f"

# 폴더 내 모든 영상에 LUT 적용
for %f in (*.mp4) do ffmpeg -i "%f" -vf "lut3d=file='cinematic.cube'" -c:a copy "graded\%f"
```

---

## 10. 인코딩 프리셋

### 용도별 권장 설정

| 용도 | 명령 옵션 |
|------|----------|
| **YouTube 1080p** | `-c:v libx264 -preset slow -crf 18 -c:a aac -b:a 256k` |
| **YouTube 4K** | `-c:v libx264 -preset slow -crf 16 -c:a aac -b:a 320k` |
| **웹용 (경량)** | `-c:v libx264 -preset fast -crf 28 -c:a aac -b:a 128k` |
| **Instagram Reels** | `-vf "scale=1080:1920" -c:v libx264 -crf 23 -c:a aac -b:a 192k` |
| **Discord (8MB 이하)** | `-c:v libx264 -preset medium -crf 32 -c:a aac -b:a 96k` |
| **보관용 (고화질)** | `-c:v libx264 -preset veryslow -crf 15 -c:a flac` |
| **ProRes (편집용)** | `-c:v prores_ks -profile:v 3 -c:a pcm_s16le` |
| **WebM (웹)** | `-c:v libvpx-vp9 -crf 30 -b:v 0 -c:a libopus` |

### CRF 가이드
- **0**: 무손실 (파일 매우 큼)
- **15-17**: 시각적 무손실 (보관용)
- **18-23**: 고품질 (YouTube, 일반 용도) ← **권장 기본값: 23**
- **24-28**: 중간 (웹, SNS)
- **29-35**: 저화질 (미리보기, 디스코드)
- **51**: 최저 화질

---

## 11. D:\참고용 에셋 활용

| 폴더 | 활용 방법 |
|------|----------|
| **LUTs** | `lut3d=file='D:/참고용/LUTs/파일명.cube'` |
| **효과음** | `adelay` + `amix`로 특정 시간에 삽입 |
| **오버레이** | `overlay` 필터로 영상 위에 합성 |
| **트렌지션** | `xfade` 또는 오버레이 영상으로 전환 |
| **배경영상** | `overlay`로 메인 영상 뒤에 배치 |
| **형광아이콘** | `overlay` + `enable`로 특정 시간에 표시 |
| **움직이는이모지** | GIF/웹엠 → `overlay` + `enable` |
| **애니메이션** | 영상 오버레이 또는 `xfade` 트렌지션 |

---

## 12. 문제 해결

| 문제 | 원인 | 해결 |
|------|------|------|
| `No such filter: 'xxx'` | ffmpeg 빌드에 필터 없음 | 최신 ffmpeg 설치 |
| 오디오/영상 싱크 어긋남 | VFR (가변 프레임레이트) | `-vsync cfr` 추가 |
| `Output file is empty` | `-ss`를 `-i` 뒤에 사용 | `-ss`를 `-i` 앞에 배치 |
| 한글 자막 깨짐 | 폰트/인코딩 문제 | `force_style='FontName=Malgun Gothic'` |
| 파일이 너무 큼 | CRF 값이 낮음 | CRF 23~28로 조정 |
| `Permission denied` | 파일 잠김 | 출력 파일을 다른 이름으로 |
| 트렌지션 안 됨 | 해상도/프레임레이트 불일치 | `scale` + `fps` 필터로 통일 |

---

## 13. 작업 원칙

1. **원본 백업**: 편집 전 원본 파일을 복사하거나, 출력 파일명을 다르게
2. **ffprobe 먼저**: 소스 정보 확인 후 적절한 필터/코덱 선택
3. **`-c copy` 우선**: 재인코딩 불필요하면 스트림 복사 (빠르고 무손실)
4. **테스트 렌더**: 전체 영상 전 `-t 10`으로 10초만 테스트
5. **경로 따옴표**: 공백 포함 경로는 `"큰따옴표"`로 감싸기
6. **Windows 경로**: ffmpeg 필터 내에서는 `/` 또는 `\\` 사용 (`\` 아님)
7. **한글 폰트**: `fontfile='C\:/Windows/Fonts/malgun.ttf'` (콜론 이스케이프)
