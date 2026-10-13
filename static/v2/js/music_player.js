// =============================================================================
// DAON BGM Music Player Module (2026-10-12)
// 상단 헤더 BGM 미니 플레이어: 폴더/파일 드래그&드롭, 플레이리스트, 볼륨/셔플/반복
// =============================================================================

export class BgmMusicPlayer {
  constructor() {
    this.audio = new Audio();
    this.playlist = []; // [{ id, name, file, url, duration }]
    this.currentIndex = -1;
    this.isPlaying = false;
    this.repeatMode = 'all'; // 'all', 'one', 'off'
    this.isShuffle = false;
    this.shuffleIndices = [];
    this.volume = parseFloat(localStorage.getItem('daon_bgm_volume') || '0.7');

    this.audio.volume = this.volume;
    this.setupAudioListeners();
  }

  setupAudioListeners() {
    this.audio.addEventListener('timeupdate', () => this.onTimeUpdate());
    this.audio.addEventListener('ended', () => this.onTrackEnded());
    this.audio.addEventListener('play', () => {
      this.isPlaying = true;
      this.updatePlayStateUI();
    });
    this.audio.addEventListener('pause', () => {
      this.isPlaying = false;
      this.updatePlayStateUI();
    });
    this.audio.addEventListener('loadedmetadata', () => {
      if (this.currentIndex >= 0 && this.playlist[this.currentIndex]) {
        this.playlist[this.currentIndex].duration = this.audio.duration;
        this.updateTimeDisplay();
      }
    });
    this.audio.addEventListener('error', (e) => {
      console.warn('[BGM Player] Audio playback error:', e);
      this.onTrackEnded(); // 다음 곡으로 이동
    });
  }

  initUI() {
    this.container = document.getElementById('bgm-player-container');
    this.bar = document.getElementById('bgm-player-bar');
    this.titleEl = document.getElementById('bgm-title');
    this.timeEl = document.getElementById('bgm-time');
    this.progressBar = document.getElementById('bgm-progress-bar');
    this.progressContainer = document.getElementById('bgm-progress-container');
    this.playBtn = document.getElementById('bgm-play-btn');
    this.playIcon = document.getElementById('bgm-play-icon');
    this.prevBtn = document.getElementById('bgm-prev-btn');
    this.nextBtn = document.getElementById('bgm-next-btn');
    this.fileInput = document.getElementById('bgm-file-input');
    this.playlistBtn = document.getElementById('bgm-playlist-toggle-btn');
    this.popover = document.getElementById('bgm-playlist-popover');
    this.trackListEl = document.getElementById('bgm-track-list');
    this.trackCountEl = document.getElementById('bgm-track-count');
    this.popoverCountEl = document.getElementById('bgm-popover-count');
    this.volumeSlider = document.getElementById('bgm-volume-slider');
    this.volIcon = document.getElementById('bgm-vol-icon');
    this.shuffleBtn = document.getElementById('bgm-shuffle-btn');
    this.repeatBtn = document.getElementById('bgm-repeat-btn');
    this.eqBars = document.querySelectorAll('.bgm-bar');

    if (!this.container) return;

    this.bindEvents();
    this.updatePlayStateUI();
    this.updatePlaylistUI();

    if (this.volumeSlider) {
      this.volumeSlider.value = this.volume;
      this.updateVolumeIcon();
    }
  }

  bindEvents() {
    // 재생/일시정지
    this.playBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.togglePlay();
    });

    // 이전/다음
    this.prevBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.playPrev();
    });
    this.nextBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.playNext();
    });

    // 시크바 클릭
    this.progressContainer?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!this.audio.duration || isNaN(this.audio.duration)) return;
      const rect = this.progressContainer.getBoundingClientRect();
      const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      this.audio.currentTime = pos * this.audio.duration;
    });

    // 플레이리스트 토글
    this.playlistBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.togglePopover();
    });

    // 팝오버 외부 클릭 시 닫기
    document.addEventListener('click', (e) => {
      if (this.popover && !this.popover.classList.contains('hidden')) {
        if (!this.popover.contains(e.target) && !this.playlistBtn.contains(e.target)) {
          this.popover.classList.add('hidden');
        }
      }
    });

    // 파일 선택 버튼
    const addBtn = document.getElementById('bgm-add-files-btn');
    addBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.fileInput?.click();
    });

    this.fileInput?.addEventListener('change', (e) => {
      if (this.fileInput.files && this.fileInput.files.length > 0) {
        this.addFiles(Array.from(this.fileInput.files));
        this.fileInput.value = '';
      }
    });

    // 목록 비우기
    document.getElementById('bgm-clear-playlist-btn')?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.clearPlaylist();
    });

    // 볼륨 조절
    this.volumeSlider?.addEventListener('input', (e) => {
      this.setVolume(parseFloat(e.target.value));
    });

    this.volIcon?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggleMute();
    });

    // 셔플 & 반복
    this.shuffleBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggleShuffle();
    });

    this.repeatBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggleRepeat();
    });

    // 🎧 폴더 및 파일 드래그 & 드롭 이벤트 바인딩
    this.setupDragAndDrop();
  }

  setupDragAndDrop() {
    const dropTargets = [this.bar, this.popover, this.container].filter(Boolean);

    dropTargets.forEach(target => {
      ['dragenter', 'dragover'].forEach(name => {
        target.addEventListener(name, (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.bar.classList.add('border-primary', 'bg-primary/[0.08]', 'scale-[1.02]');
        }, false);
      });

      ['dragleave', 'drop'].forEach(name => {
        target.addEventListener(name, (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.bar.classList.remove('border-primary', 'bg-primary/[0.08]', 'scale-[1.02]');
        }, false);
      });

      target.addEventListener('drop', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.bar.classList.remove('border-primary', 'bg-primary/[0.08]', 'scale-[1.02]');

        const items = e.dataTransfer?.items;
        if (items && items.length > 0) {
          const collectedFiles = [];
          const queue = [];

          for (let i = 0; i < items.length; i++) {
            const item = items[i];
            if (item.webkitGetAsEntry) {
              const entry = item.webkitGetAsEntry();
              if (entry) queue.push(this.traverseFileTree(entry, collectedFiles));
            } else if (item.kind === 'file') {
              const f = item.getAsFile();
              if (f && this.isAudioFile(f.name)) collectedFiles.push(f);
            }
          }

          if (queue.length > 0) {
            await Promise.all(queue);
          }

          if (collectedFiles.length > 0) {
            this.addFiles(collectedFiles);
          }
        } else if (e.dataTransfer?.files && e.dataTransfer.files.length > 0) {
          const files = Array.from(e.dataTransfer.files).filter(f => this.isAudioFile(f.name));
          if (files.length > 0) {
            this.addFiles(files);
          }
        }
      }, false);
    });
  }

  // 폴더 내부 재귀 탐색
  traverseFileTree(item, fileList) {
    return new Promise((resolve) => {
      if (item.isFile) {
        item.file((file) => {
          if (this.isAudioFile(file.name)) {
            fileList.push(file);
          }
          resolve();
        }, () => resolve());
      } else if (item.isDirectory) {
        const dirReader = item.createReader();
        const readEntries = () => {
          dirReader.readEntries(async (entries) => {
            if (entries.length === 0) {
              resolve();
            } else {
              const promises = entries.map(entry => this.traverseFileTree(entry, fileList));
              await Promise.all(promises);
              readEntries(); // entries가 100개 이상일 수 있으므로 반복
            }
          }, () => resolve());
        };
        readEntries();
      } else {
        resolve();
      }
    });
  }

  isAudioFile(name) {
    return /\.(mp3|wav|flac|ogg|m4a|aac|opus|weba|wma)$/i.test(name || '');
  }

  addFiles(files) {
    const audioFiles = files.filter(f => this.isAudioFile(f.name));
    if (audioFiles.length === 0) return;

    const startIndex = this.playlist.length;
    audioFiles.forEach(f => {
      const cleanName = f.name.replace(/\.[^/.]+$/, ''); // 확장자 제거
      this.playlist.push({
        id: 'track_' + Math.random().toString(36).substr(2, 9),
        name: cleanName,
        file: f,
        url: URL.createObjectURL(f),
        duration: 0
      });
    });

    this.updatePlaylistUI();

    // 플레이어가 멈춰있거나 목록이 비어있던 경우, 방금 추가된 첫 곡 바로 재생
    if (this.currentIndex === -1 || !this.isPlaying) {
      this.playTrack(startIndex);
    }
  }

  playTrack(index) {
    if (index < 0 || index >= this.playlist.length) return;

    this.currentIndex = index;
    const track = this.playlist[index];

    this.audio.src = track.url;
    this.audio.play().catch(e => {
      console.warn('[BGM Player] Play failed (user interaction required):', e);
    });

    if (this.titleEl) {
      this.titleEl.textContent = track.name;
      this.titleEl.title = track.name;
    }

    this.updatePlayStateUI();
    this.updatePlaylistUI();
  }

  togglePlay() {
    if (this.playlist.length === 0) {
      this.fileInput?.click();
      return;
    }
    if (this.currentIndex === -1) {
      this.playTrack(0);
      return;
    }

    if (this.isPlaying) {
      this.audio.pause();
    } else {
      this.audio.play().catch(e => console.warn('[BGM] Play error:', e));
    }
  }

  playNext() {
    if (this.playlist.length === 0) return;

    if (this.isShuffle) {
      let nextIdx = Math.floor(Math.random() * this.playlist.length);
      if (this.playlist.length > 1 && nextIdx === this.currentIndex) {
        nextIdx = (nextIdx + 1) % this.playlist.length;
      }
      this.playTrack(nextIdx);
      return;
    }

    let nextIdx = this.currentIndex + 1;
    if (nextIdx >= this.playlist.length) {
      if (this.repeatMode === 'off') {
        this.audio.pause();
        this.audio.currentTime = 0;
        this.isPlaying = false;
        this.updatePlayStateUI();
        return;
      }
      nextIdx = 0;
    }
    this.playTrack(nextIdx);
  }

  playPrev() {
    if (this.playlist.length === 0) return;
    if (this.audio.currentTime > 3) {
      // 3초 이상 재생 중이면 처음으로 이동
      this.audio.currentTime = 0;
      return;
    }

    let prevIdx = this.currentIndex - 1;
    if (prevIdx < 0) {
      prevIdx = this.playlist.length - 1;
    }
    this.playTrack(prevIdx);
  }

  onTrackEnded() {
    if (this.repeatMode === 'one') {
      this.audio.currentTime = 0;
      this.audio.play();
      return;
    }
    this.playNext();
  }

  onTimeUpdate() {
    if (!this.audio.duration || isNaN(this.audio.duration)) return;
    const progress = (this.audio.currentTime / this.audio.duration) * 100;
    if (this.progressBar) {
      this.progressBar.style.width = `${progress}%`;
    }
    this.updateTimeDisplay();
  }

  updateTimeDisplay() {
    if (!this.timeEl) return;
    const cur = this.formatTime(this.audio.currentTime || 0);
    const dur = this.formatTime(this.audio.duration || 0);
    this.timeEl.textContent = `${cur} / ${dur}`;
  }

  formatTime(sec) {
    if (isNaN(sec) || sec < 0) return '00:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }

  setVolume(vol) {
    this.volume = Math.max(0, Math.min(1, vol));
    this.audio.volume = this.volume;
    localStorage.setItem('daon_bgm_volume', String(this.volume));
    if (this.volumeSlider) this.volumeSlider.value = this.volume;
    this.updateVolumeIcon();
  }

  toggleMute() {
    if (this.audio.volume > 0) {
      this.prevVolume = this.audio.volume;
      this.setVolume(0);
    } else {
      this.setVolume(this.prevVolume || 0.7);
    }
  }

  updateVolumeIcon() {
    if (!this.volIcon) return;
    if (this.volume === 0) {
      this.volIcon.textContent = 'volume_off';
    } else if (this.volume < 0.5) {
      this.volIcon.textContent = 'volume_down';
    } else {
      this.volIcon.textContent = 'volume_up';
    }
  }

  toggleShuffle() {
    this.isShuffle = !this.isShuffle;
    if (this.shuffleBtn) {
      this.shuffleBtn.className = this.isShuffle
        ? 'p-1 rounded bg-primary/10 text-primary font-bold cursor-pointer transition-colors'
        : 'p-1 rounded hover:bg-black/5 text-on-surface-variant cursor-pointer transition-colors';
    }
  }

  toggleRepeat() {
    if (this.repeatMode === 'all') {
      this.repeatMode = 'one';
      if (this.repeatBtn) {
        this.repeatBtn.innerHTML = '<span class="material-symbols-outlined text-[15px]">repeat_one</span>';
        this.repeatBtn.className = 'p-1 rounded bg-primary/10 text-primary font-bold cursor-pointer transition-colors';
        this.repeatBtn.title = '한 곡 반복';
      }
    } else if (this.repeatMode === 'one') {
      this.repeatMode = 'off';
      if (this.repeatBtn) {
        this.repeatBtn.innerHTML = '<span class="material-symbols-outlined text-[15px]">repeat</span>';
        this.repeatBtn.className = 'p-1 rounded hover:bg-black/5 text-on-surface-variant/50 cursor-pointer transition-colors';
        this.repeatBtn.title = '반복 끄기';
      }
    } else {
      this.repeatMode = 'all';
      if (this.repeatBtn) {
        this.repeatBtn.innerHTML = '<span class="material-symbols-outlined text-[15px]">repeat</span>';
        this.repeatBtn.className = 'p-1 rounded bg-primary/10 text-primary font-bold cursor-pointer transition-colors';
        this.repeatBtn.title = '전체 반복';
      }
    }
  }

  togglePopover() {
    if (!this.popover) return;
    const isHidden = this.popover.classList.contains('hidden');
    if (isHidden) {
      this.popover.classList.remove('hidden');
      this.popover.classList.add('flex');
    } else {
      this.popover.classList.add('hidden');
      this.popover.classList.remove('flex');
    }
  }

  clearPlaylist() {
    this.audio.pause();
    this.audio.currentTime = 0;
    this.isPlaying = false;
    this.playlist.forEach(t => URL.revokeObjectURL(t.url));
    this.playlist = [];
    this.currentIndex = -1;

    if (this.titleEl) this.titleEl.textContent = '음악 끌어놓기 🎧';
    if (this.progressBar) this.progressBar.style.width = '0%';
    if (this.timeEl) this.timeEl.textContent = '00:00';

    this.updatePlayStateUI();
    this.updatePlaylistUI();
  }

  removeTrack(index) {
    if (index < 0 || index >= this.playlist.length) return;
    const removed = this.playlist.splice(index, 1)[0];
    if (removed && removed.url) URL.revokeObjectURL(removed.url);

    if (index === this.currentIndex) {
      if (this.playlist.length === 0) {
        this.clearPlaylist();
        return;
      }
      this.playTrack(Math.min(index, this.playlist.length - 1));
    } else if (index < this.currentIndex) {
      this.currentIndex--;
    }
    this.updatePlaylistUI();
  }

  updatePlayStateUI() {
    if (this.playIcon) {
      this.playIcon.textContent = this.isPlaying ? 'pause' : 'play_arrow';
    }

    // 플레이어 바에 is-playing 클래스 토글 (오로라 네온 글로우 & EQ 파동 애니메이션 가동)
    if (this.bar) {
      this.bar.classList.toggle('is-playing', this.isPlaying);
    }
  }

  updatePlaylistUI() {
    const count = this.playlist.length;
    if (this.trackCountEl) {
      if (count > 0) {
        this.trackCountEl.textContent = count;
        this.trackCountEl.classList.remove('hidden');
      } else {
        this.trackCountEl.classList.add('hidden');
      }
    }
    if (this.popoverCountEl) {
      this.popoverCountEl.textContent = `(${count}곡)`;
    }

    if (!this.trackListEl) return;

    if (count === 0) {
      this.trackListEl.innerHTML = `
        <div class="text-center py-6 text-on-surface-variant/70 text-[11px]">
          음악 파일이나 폴더를 끌어다 놓으세요 🎧
        </div>
      `;
      return;
    }

    this.trackListEl.innerHTML = '';
    this.playlist.forEach((track, idx) => {
      const isCurrent = idx === this.currentIndex;
      const row = document.createElement('div');
      row.className = `flex items-center justify-between px-2 py-1.5 rounded-[6px] cursor-pointer transition-colors group ${
        isCurrent
          ? 'bg-gradient-to-r from-violet-600/15 to-pink-600/10 text-violet-600 dark:text-violet-300 font-semibold border border-violet-500/25'
          : 'hover:bg-black/5 dark:hover:bg-white/5 text-on-surface'
      }`;

      row.innerHTML = `
        <div class="flex items-center gap-1.5 min-w-0 flex-1">
          <span class="material-symbols-outlined text-[15px] ${isCurrent ? 'text-violet-500' : 'text-on-surface-variant/60'}">
            ${isCurrent && this.isPlaying ? 'equalizer' : 'music_note'}
          </span>
          <span class="truncate text-[11.5px]">${idx + 1}. ${track.name}</span>
        </div>
        <button type="button" class="text-on-surface-variant/40 hover:text-red-600 p-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity delete-track-btn" data-idx="${idx}" title="삭제">
          <span class="material-symbols-outlined text-[13px]">close</span>
        </button>
      `;

      row.addEventListener('click', (e) => {
        if (e.target.closest('.delete-track-btn')) return;
        this.playTrack(idx);
      });

      row.querySelector('.delete-track-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        this.removeTrack(idx);
      });

      this.trackListEl.appendChild(row);
    });
  }
}

// 싱글톤 인스턴스 생성 및 전역 등록
export const bgmPlayer = new BgmMusicPlayer();
