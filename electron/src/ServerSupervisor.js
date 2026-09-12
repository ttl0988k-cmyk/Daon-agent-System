// @ts-check
/**
 * ServerSupervisor
 * Manages the lifecycle, health checks, watchdog, and process supervisor
 * for the Python backend (server.exe) and TTS sub-process.
 */
const { spawn, exec, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');
const net = require('net');
const os = require('os');
const { app, powerMonitor } = require('electron');

class ServerSupervisor {
  /**
   * @param {Object} options
   * @param {Function} options.mlog
   * @param {Function} options.merr
   * @param {Function} [options.onServerRestarted] - callback when watchdog restarts server
   */
  constructor({ mlog, merr, onServerRestarted }) {
    this.mlog = mlog || console.log;
    this.merr = merr || console.error;
    this.onServerRestarted = onServerRestarted || null;

    this.pythonProcess = null;
    this.ttsProcess = null;
    this.watchdogTimer = null;
    this.watchdogRestartCount = 0;
    this.watchdogSuppressUntil = 0;
    this._watchdogReloadPending = false;
    this.isQuitting = false;
    this.selfModifyRestartActive = false;

    this.WATCHDOG_INTERVAL = 30_000;
    this.MAX_RESTARTS = 3;
    this.POST_SWAP_HEALTH_GRACE_MS = 120_000;

    // [재시작 루프 방어 2026-09-12] 즉사-재스폰 무한 루프 방지.
    // 포트 9090 을 붙잡은 잔존 server.exe 가 있으면 새 스폰이 바인딩 실패로
    // 즉사하고 exit 핸들러가 다시 2초 후 스폰 → 무한 반복(실측 1,998회,
    // server.log 122MB, _MEI 11개 누적). 세 겹으로 차단한다:
    //   ① 재스폰 전 killPortOwner(port) 로 잔존 점유자 제거 (C-1)
    //   ② 스폰 후 CRASH_WINDOW_MS 내 종료 = '즉사'로 계상, 지수 백오프 (C-2)
    //   ③ MAX_CRASH_STREAK 연속 즉사 시 자동 재시작 중단 = 서킷브레이커 (C-2)
    this.CRASH_WINDOW_MS = 15_000;
    this.MAX_CRASH_STREAK = 8;
    this.MAX_RESTART_BACKOFF_MS = 60_000;
    this.crashStreak = 0;
    this._lastSpawnAt = 0;
  }

  // 자동 재시작 단일 관문 — 즉사 계상 · 백오프 · 서킷브레이커 · 잔존 점유자 제거를
  // 한 곳에서 처리한다. exit / error / spawn-catch 모든 경로가 이 함수만 호출한다.
  _scheduleAutoRespawn(port, cause) {
    if (this.isQuitting || this.selfModifyRestartActive) return;
    if (this.crashStreak >= this.MAX_CRASH_STREAK) {
      this.merr(
        `[ServerSupervisor] CIRCUIT BREAKER open — ${this.crashStreak} consecutive rapid crashes. `
        + 'Auto-restart halted. Manual restart required.'
      );
      return;
    }
    const delay = Math.min(
      2000 * Math.pow(2, Math.max(0, this.crashStreak - 1)),
      this.MAX_RESTART_BACKOFF_MS
    );
    this.mlog(
      `[ServerSupervisor] ${cause} — auto-restart in ${Math.round(delay / 1000)}s `
      + `(rapid-crash streak ${this.crashStreak}/${this.MAX_CRASH_STREAK}).`
    );
    setTimeout(() => {
      if (this.isQuitting || this.pythonProcess) return;
      // ① 잔존 포트 점유자 제거 — 없으면 새 스폰이 EADDRINUSE 로 즉사한다.
      try { this.killPortOwner(port); } catch (_) { }
      this.startPythonProcess(port);
    }, delay);
  }

  // ── Port & Socket Utilities ──

  findFreePort(startPort) {
    return new Promise((resolve, reject) => {
      const server = net.createServer();
      server.listen(startPort, '127.0.0.1', () => {
        const port = server.address().port;
        server.close(() => resolve(port));
      });
      server.on('error', (err) => {
        if (err.code === 'EADDRINUSE') {
          resolve(this.findFreePort(startPort + 1));
        } else {
          reject(err);
        }
      });
    });
  }

  isPortListening(port, timeoutMs = 2000) {
    return new Promise((resolve) => {
      const socket = new net.Socket();
      let settled = false;
      const done = (v) => {
        if (!settled) {
          settled = true;
          try { socket.destroy(); } catch (_) { }
          resolve(v);
        }
      };
      socket.once('connect', () => done(true));
      socket.once('timeout', () => done(false));
      socket.once('error', () => done(false));
      socket.setTimeout(timeoutMs);
      socket.connect(port, '127.0.0.1');
    });
  }

  // ── Unified Health Check Engine ──

  /**
   * Probe /health and return parsed JSON or null
   */
  probeHealth(port, timeoutMs = 1000) {
    return new Promise((resolve) => {
      let settled = false;
      const done = (v) => { if (!settled) { settled = true; resolve(v); } };
      const req = http.get({ host: '127.0.0.1', port, path: '/health', family: 4 }, (res) => {
        let body = '';
        res.on('data', (chunk) => { body += chunk; });
        res.on('end', () => {
          try {
            done(JSON.parse(body));
          } catch (_) {
            done(null);
          }
        });
      });
      req.on('error', () => done(null));
      req.setTimeout(timeoutMs, () => { req.destroy(); done(null); });
    });
  }

  /**
   * Two-phase probe to avoid false positives on busy server
   */
  async probeHealthStable(port) {
    const first = await this.probeHealth(port, 3000);
    if (first && first.healthy && first.pid) return first;
    await new Promise(r => setTimeout(r, 500));
    const second = await this.probeHealth(port, 4000);
    if (second && second.healthy && second.pid) return second;
    return null;
  }

  /**
   * Long-grace probe for slow initial extraction (PyInstaller _MEI extraction)
   */
  async probeHealthGrace(port, opts = {}) {
    const maxWaitMs = opts.maxWaitMs != null ? opts.maxWaitMs : this.POST_SWAP_HEALTH_GRACE_MS;
    const pollMs = opts.pollMs != null ? opts.pollMs : 1000;
    const stableHits = opts.stableHits != null ? opts.stableHits : 3;
    const timeoutMs = opts.timeoutMs != null ? opts.timeoutMs : 1500;
    const isAlive = typeof opts.isAlive === 'function' ? opts.isAlive : null;
    const deadline = Date.now() + maxWaitMs;
    let hits = 0;
    let lastLogAt = 0;

    while (Date.now() < deadline) {
      if (isAlive && !isAlive()) {
        this.mlog('[ServerSupervisor] grace health aborted — server process already exited.');
        return null;
      }
      const h = await this.probeHealth(port, timeoutMs);
      if (h && h.healthy && h.pid) {
        hits += 1;
        if (hits >= stableHits) return h;
      } else {
        if (hits > 0) this.mlog('[ServerSupervisor] grace health: stability reset (flapping).');
        hits = 0;
        if (Date.now() - lastLogAt > 10000) {
          lastLogAt = Date.now();
          this.mlog(`[ServerSupervisor] grace health: waiting for server (${Math.max(0, Math.round((deadline - Date.now()) / 1000))}s left)...`);
        }
      }
      await new Promise((r) => setTimeout(r, pollMs));
    }
    this.mlog(`[ServerSupervisor] grace health: deadline exceeded (${maxWaitMs}ms).`);
    return null;
  }

  /**
   * Poll health until 200 OK or timeout
   */
  checkServerHealth(port, retries = 180, delayMs = 1000) {
    return new Promise((resolve, reject) => {
      let attempted = 0;
      const poll = () => {
        attempted++;
        const req = http.get({ host: '127.0.0.1', port, path: '/health', family: 4 }, (res) => {
          if (res.statusCode === 200) {
            resolve(true);
          } else if (attempted >= retries) {
            reject(new Error(`Server returned status code ${res.statusCode}`));
          } else {
            setTimeout(poll, delayMs);
          }
        });
        req.on('error', (err) => {
          if (attempted >= retries) {
            reject(err);
          } else {
            setTimeout(poll, delayMs);
          }
        });
        req.setTimeout(1000, () => req.destroy());
      };
      poll();
    });
  }

  // ── Process Finding & Lifecycle ──

  findServerExe() {
    const candidates = [
      path.join(process.resourcesPath, 'server.exe'),
      path.join(process.resourcesPath, 'server', 'server.exe'),
      path.join(path.dirname(process.execPath), 'server.exe'),
      path.join(__dirname, '..', '..', 'dist', 'server.exe'),
      path.join(__dirname, '..', '..', 'server.exe'),
      path.join(__dirname, '..', 'server.exe'),
    ];

    for (const p of candidates) {
      if (fs.existsSync(p)) return p;
    }
    return null;
  }

  killProcessTree(pid) {
    if (!pid) return;
    try {
      if (process.platform === 'win32') {
        execSync(`taskkill /pid ${pid} /T /F 2>nul`, { windowsHide: true });
        this.mlog(`[ServerSupervisor] Successfully killed process tree for PID: ${pid}`);
      } else {
        process.kill(-pid, 'SIGKILL');
      }
    } catch (_) { }
  }

  findPortOwnerPid(port) {
    if (process.platform !== 'win32') return null;
    try {
      const out = execSync(`netstat -ano -p TCP`, { encoding: 'utf-8', windowsHide: true });
      const lines = out.split(/\r?\n/);
      for (const line of lines) {
        if (line.includes(`:${port}`) && line.includes('LISTENING')) {
          const parts = line.trim().split(/\s+/);
          const pid = parseInt(parts[parts.length - 1], 10);
          if (pid && !isNaN(pid) && pid > 0) {
            return pid;
          }
        }
      }
    } catch (_) { }
    return null;
  }

  killPortOwner(port) {
    const pid = this.findPortOwnerPid(port);
    if (pid && pid > 0) {
      this.mlog(`[ServerSupervisor] Found process PID ${pid} listening on port ${port}. Terminating...`);
      this.killProcessTree(pid);
      return true;
    }
    return false;
  }


  isProcessAlive(pid) {
    if (!pid) return false;
    try {
      process.kill(pid, 0);
      return true;
    } catch (e) {
      return !!(e && e.code === 'EPERM');
    }
  }

  adoptRunningServer(pid, port) {
    this.mlog(`[ServerSupervisor] Adopting existing healthy server PID: ${pid} on port ${port}`);
    this.pythonProcess = {
      pid,
      _adopted: true,
      stdout: { on: () => { } },
      stderr: { on: () => { } },
      on: () => { }
    };
    return this.pythonProcess;
  }

  startPythonProcess(port) {
    if (this.isQuitting) return null;
    const env = { ...process.env, BROWSER_CDP_URL: 'ws://127.0.0.1:9222' };
    const exePath = this.findServerExe();

    try {
      if (exePath) {
        this.mlog(`[ServerSupervisor] Spawning server executable: ${exePath} on port ${port}`);
        const cwd = path.dirname(exePath);
        this.pythonProcess = spawn(exePath, ['--no-browser', '--port', port.toString()], {
          cwd,
          env,
          windowsHide: true,
          stdio: ['pipe', 'pipe', 'pipe']
        });
      } else {
        this.mlog(`[ServerSupervisor] server.exe not found. Falling back to python server.py...`);
        this.pythonProcess = spawn('python', ['server.py', '--no-browser', '--port', port.toString()], {
          cwd: path.join(__dirname, '..', '..'),
          env,
          windowsHide: true,
          stdio: ['pipe', 'pipe', 'pipe']
        });
      }
    } catch (err) {
      this.merr(`[ServerSupervisor] startPythonProcess spawn failed: ${err.message}`);
      this.pythonProcess = null;
      this.crashStreak += 1;
      this._scheduleAutoRespawn(port, 'spawn failed');
      return null;
    }

    if (!this.pythonProcess) return null;
    this._lastSpawnAt = Date.now();

    this.pythonProcess.on('error', (err) => {
      this.merr(`[ServerSupervisor] Python process error: ${err && err.message}`);
      this.pythonProcess = null;
      this.crashStreak += 1;
      this._scheduleAutoRespawn(port, 'Python process error');
    });

    try {
      const serverLogPath = path.join(app.getPath('userData'), 'server.log');
      const serverLogStream = fs.createWriteStream(serverLogPath, { flags: 'a' });
      this.pythonProcess.stdout.on('data', (data) => {
        try { serverLogStream.write(`[STDOUT] ${data}`); } catch (_) { }
      });
      this.pythonProcess.stderr.on('data', (data) => {
        try { serverLogStream.write(`[STDERR] ${data}`); } catch (_) { }
      });
    } catch (_) { }

    this.pythonProcess.on('exit', (code, signal) => {
      this.merr(`[ServerSupervisor] Main Python server exited (code=${code}, signal=${signal}, isQuitting=${this.isQuitting})`);
      this.pythonProcess = null;
      if (this.isQuitting) return;
      if (this.selfModifyRestartActive) {
        this.mlog('[ServerSupervisor] Server exit during self-modify restart — orchestrator owns respawn.');
        return;
      }

      // ② 즉사 판정: 스폰 직후 짧은 생존 후 종료 = 크래시로 계상(백오프 대상).
      //    CRASH_WINDOW_MS 이상 생존했다 죽으면 정상 종료로 보고 스트릭을 리셋한다.
      const aliveMs = this._lastSpawnAt ? Date.now() - this._lastSpawnAt : Infinity;
      if (aliveMs < this.CRASH_WINDOW_MS) {
        this.crashStreak += 1;
      } else {
        this.crashStreak = 0;
      }
      this._scheduleAutoRespawn(port, 'Server exit detected');
    });

    // Priority boost on Windows
    if (process.platform === 'win32' && this.pythonProcess.pid) {
      try {
        exec(`powershell -NoProfile -Command "(Get-Process -Id ${this.pythonProcess.pid}).PriorityClass = 'AboveNormal'"`, { windowsHide: true });
      } catch (_) { }
    }

    return this.pythonProcess;
  }

  startTtsProcess(port) {
    if (this.isQuitting) return null;
    const isPackaged = app.isPackaged;

    try {
      if (isPackaged) {
        const ttsExePath = this.findServerExe();
        const cwd = path.dirname(ttsExePath);
        this.ttsProcess = spawn(ttsExePath, ['--tts-mode', '--tts-port', port.toString()], {
          cwd,
          windowsHide: true,
          stdio: ['pipe', 'pipe', 'pipe']
        });
      } else {
        const ttsScript = path.join(__dirname, '..', '..', 'tts_server.py');
        const scriptToRun = fs.existsSync(ttsScript) ? 'tts_server.py' : 'server.py';
        const scriptArgs = scriptToRun === 'tts_server.py'
          ? ['tts_server.py', '--port', port.toString()]
          : ['server.py', '--tts-mode', '--tts-port', port.toString()];

        this.ttsProcess = spawn('python', scriptArgs, {
          cwd: path.join(__dirname, '..', '..'),
          windowsHide: true,
          stdio: ['pipe', 'pipe', 'pipe']
        });
      }
    } catch (err) {
      this.merr(`[ServerSupervisor] startTtsProcess spawn failed: ${err.message}`);
      this.ttsProcess = null;
      if (!this.isQuitting && !this.selfModifyRestartActive) {
        setTimeout(() => {
          if (!this.isQuitting && !this.ttsProcess) this.startTtsProcess(port);
        }, 1500);
      }
      return null;
    }

    if (!this.ttsProcess) return null;

    this.ttsProcess.on('error', (err) => {
      this.merr(`[ServerSupervisor] TTS process error: ${err && err.message}`);
      this.ttsProcess = null;
    });

    this.ttsProcess.on('exit', (code, signal) => {
      this.merr(`[ServerSupervisor] TTS server exited (code=${code}, signal=${signal})`);
      this.ttsProcess = null;
      if (!this.isQuitting && !this.selfModifyRestartActive) {
        setTimeout(() => {
          if (!this.isQuitting && !this.ttsProcess) {
            this.startTtsProcess(port);
          }
        }, 2000);
      }
    });

    if (process.platform === 'win32' && this.ttsProcess.pid) {
      try {
        exec(`powershell -NoProfile -Command "(Get-Process -Id ${this.ttsProcess.pid}).PriorityClass = 'BelowNormal'"`, { windowsHide: true });
      } catch (_) { }
    }

    return this.ttsProcess;
  }

  // ── Watchdog Management ──

  async handleWatchdogFailure(port) {
    if (this.selfModifyRestartActive) return;
    if (Date.now() < this.watchdogSuppressUntil) return;

    // Check TCP port listening to avoid false positive
    const listeningNow = await this.isPortListening(port, 3000);
    if (listeningNow) {
      this.mlog(`[Watchdog] Health check /health probe failed but TCP port ${port} is LISTENING — server alive (false positive). Resetting counter.`);
      this.watchdogRestartCount = 0;
      return;
    }

    this.watchdogRestartCount++;
    this.merr(`[Watchdog] Health check failure (${this.watchdogRestartCount}/${this.MAX_RESTARTS})`);
    if (this.watchdogRestartCount < this.MAX_RESTARTS) return;

    // Confirm dead with stable probe
    const confirm = await this.probeHealthStable(port);
    if (confirm && confirm.healthy && confirm.pid) {
      this.mlog(`[Watchdog] ${this.MAX_RESTARTS} failures but server is ALIVE (pid=${confirm.pid}) — false positive, resetting counter.`);
      this.watchdogRestartCount = 0;
      return;
    }

    if (await this.isPortListening(port, 3000)) {
      this.mlog(`[Watchdog] Final check: TCP port ${port} is LISTENING — refusing to kill server process.`);
      this.watchdogRestartCount = 0;
      return;
    }

    this.watchdogRestartCount = 0;
    this.merr(`[Watchdog] Confirmed server dead after ${this.MAX_RESTARTS} consecutive checks. Restarting Python server...`);

    if (this.pythonProcess && this.pythonProcess.pid) {
      this.killProcessTree(this.pythonProcess.pid);
      this.pythonProcess = null;
    }
    this.startPythonProcess(port);

    if (this._watchdogReloadPending) return;
    this._watchdogReloadPending = true;

    this.checkServerHealth(port, 30, 2000).then(() => {
      this._watchdogReloadPending = false;
      if (this.onServerRestarted) this.onServerRestarted();
    }).catch(() => {
      this._watchdogReloadPending = false;
    });
  }

  startWatchdog(port) {
    if (this.watchdogTimer) return;
    this.mlog('[Watchdog] Starting health monitor (every 30s)...');
    this.watchdogRestartCount = 0;

    if (powerMonitor) {
      powerMonitor.on('resume', () => {
        this.mlog('[Watchdog] System resumed from sleep/idle - resetting watchdog...');
        this.watchdogRestartCount = 0;
        this.checkServerHealth(port, 10).catch(() => {
          this.probeHealthStable(port).then((confirm) => {
            if (confirm && confirm.healthy && confirm.pid) {
              this.mlog(`[Watchdog] Post-resume false positive — server alive (pid=${confirm.pid}), no kill.`);
              return;
            }
            this.merr('[Watchdog] Confirmed server dead after resume - restarting.');
            if (this.pythonProcess && this.pythonProcess.pid) {
              this.killProcessTree(this.pythonProcess.pid);
              this.pythonProcess = null;
            }
            this.startPythonProcess(port);
          });
        });
      });
    }

    this.watchdogTimer = setInterval(() => {
      if (this.isQuitting) return;

      // If server process was adopted, check if PID is still alive
      if (this.pythonProcess && this.pythonProcess._adopted && this.pythonProcess.pid && !this.isProcessAlive(this.pythonProcess.pid)) {
        this.merr(`[Watchdog] Adopted server PID ${this.pythonProcess.pid} died — spawning new server...`);
        this.pythonProcess = null;
        this.startPythonProcess(port);
        return;
      }

      const req = http.get({ host: '127.0.0.1', port, path: '/health', family: 4 }, (res) => {
        if (res.statusCode === 200) {
          this.watchdogRestartCount = 0;
          // 서버가 실제로 응답했으므로 즉사 스트릭을 닫는다(서킷브레이커 재무장).
          this.crashStreak = 0;
        } else {
          this.merr(`[Watchdog] Health check non-200: ${res.statusCode}`);
          this.handleWatchdogFailure(port);
        }
      });
      req.on('error', (err) => {
        this.merr(`[Watchdog] Health check request failed: ${err.message}`);
        this.handleWatchdogFailure(port);
      });
      req.setTimeout(5000, () => {
        req.destroy();
        this.merr('[Watchdog] Health check timed out');
        this.handleWatchdogFailure(port);
      });
    }, this.WATCHDOG_INTERVAL);
  }

  stopWatchdog() {
    if (this.watchdogTimer) {
      clearInterval(this.watchdogTimer);
      this.watchdogTimer = null;
      this.mlog('[Watchdog] Stopped.');
    }
  }

  shutdown() {
    this.isQuitting = true;
    this.stopWatchdog();
    if (this.pythonProcess && this.pythonProcess.pid) {
      this.killProcessTree(this.pythonProcess.pid);
      this.pythonProcess = null;
    }
    if (this.ttsProcess && this.ttsProcess.pid) {
      this.killProcessTree(this.ttsProcess.pid);
      this.ttsProcess = null;
    }
  }
}

module.exports = {
  ServerSupervisor,
};
