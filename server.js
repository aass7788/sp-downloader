const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const express = require('express');
const { WebSocketServer } = require('ws');

// ── Config ──────────────────────────────────────────────────────────
const PORT = process.env.PORT || 3456;
const YTDLP = process.platform === 'win32'
  ? path.join(__dirname, 'yt-dlp.exe')
  : 'yt-dlp'; // Linux: use system yt-dlp from PATH
const PUBLIC_DIR = path.join(__dirname, 'public');
const DEFAULT_OUTPUT = process.platform === 'win32'
  ? path.join(__dirname, 'downloads')
  : '/app/downloads';
const TMP_DIR = process.platform === 'win32' ? __dirname : '/tmp';

// ── Express ─────────────────────────────────────────────────────────
const app = express();
app.use(express.static(PUBLIC_DIR));

// Health endpoint: returns yt-dlp version
app.get('/api/health', (req, res) => {
  const proc = spawn(YTDLP, ['--version'], { stdio: ['ignore', 'pipe', 'pipe'] });
  let version = '';
  proc.stdout.on('data', d => version += d.toString());
  proc.on('close', () => res.json({ status: 'ok', version: version.trim() }));
});

// ── HTTP + WebSocket Server ─────────────────────────────────────────
const server = http.createServer(app);
const wss = new WebSocketServer({ server });

// ── Progress Parser ─────────────────────────────────────────────────
const PROGRESS_RE = /^PROGRESS:(.*?)\|(.*?)\|(.*?)\|(\d+)\|(\d+)\|(\d+)\|(.*)$/;
const DEST_RE = /^\[download\] Destination:\s*(.+)$/;

function parseProgress(line) {
  const m = line.match(PROGRESS_RE);
  if (!m) return null;
  return {
    percent: m[1].trim(),
    speed: m[2].trim(),
    eta: m[3].trim(),
    downloadedBytes: parseInt(m[4]) || 0,
    totalBytes: parseInt(m[5]) || parseInt(m[6]) || 0,
    elapsed: m[7].trim(),
  };
}

// ── WebSocket Session ───────────────────────────────────────────────
wss.on('connection', (ws) => {
  console.log('[ws] client connected');

  let ytdlpProcess = null;
  let tempFile = null;
  let cancelled = false;

  // Send handshake info
  ws.send(JSON.stringify({ type: 'info', port: PORT }));

  ws.on('message', (raw) => {
    let msg;
    try { msg = JSON.parse(raw.toString()); } catch { return; }

    if (msg.type === 'start') {
      try {
        startDownload(ws, msg);
      } catch (e) {
        console.error('[download] startDownload error:', e.message);
        ws.send(JSON.stringify({ type: 'error', message: e.message }));
      }
    } else if (msg.type === 'cancel') {
      cancelled = true;
      if (ytdlpProcess) {
        ytdlpProcess.kill('SIGTERM');
        setTimeout(() => {
          if (ytdlpProcess && !ytdlpProcess.killed) {
            try { process.kill(ytdlpProcess.pid, 'SIGKILL'); } catch {}
          }
        }, 2000);
      }
      ws.send(JSON.stringify({ type: 'cancelled' }));
    }
  });

  ws.on('close', () => {
    cancelled = true;
    if (ytdlpProcess && !ytdlpProcess.killed) {
      try { process.kill(ytdlpProcess.pid, 'SIGKILL'); } catch {}
    }
    cleanupTemp();
  });

  function cleanupTemp() {
    if (tempFile && fs.existsSync(tempFile)) {
      try { fs.unlinkSync(tempFile); } catch {}
      tempFile = null;
    }
  }

  // ── Start Download ──────────────────────────────────────────
  function startDownload(ws, config) {
    const urls = (config.urls || []).filter(Boolean);
    if (urls.length === 0) {
      ws.send(JSON.stringify({ type: 'error', message: 'No URLs provided' }));
      return;
    }

    let outputDir = config.outputDir || DEFAULT_OUTPUT;
    const disguise = config.disguise !== false;
    const userAgent = config.userAgent ||
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
    const sleepMin = config.sleepMin || 1;
    const sleepMax = config.sleepMax || 2;
    const cookiesFromBrowser = config.cookiesFromBrowser || '';
    const cookiesFile = config.cookiesFile || '';

    // Ensure output directory exists (fall back to default on error, e.g. Windows path on Linux)
    try {
      if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
    } catch (e) {
      console.log('[download] Cannot create', outputDir, '-> using default');
      ws.send(JSON.stringify({ type: 'log', level: 'warn', message: `目录不可用，已切换为默认: ${DEFAULT_OUTPUT}` }));
      outputDir = DEFAULT_OUTPUT;
      if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
    }

    // Write URLs to temp file (use /tmp on Linux because read_only fs)
    tempFile = path.join(TMP_DIR, `.temp_urls_${Date.now()}.txt`);
    fs.writeFileSync(tempFile, urls.join('\n'), 'utf-8');

    // Build yt-dlp arguments
    const args = [
      '--no-playlist',
      '--newline',
      '--impersonate', 'chrome',       // 浏览器指纹伪装，绕过 TikTok 反爬
      '--progress-template',
      'PROGRESS:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.elapsed)s',
      '--progress-delta', '1',
      '-o', path.join(outputDir, '%(title).100s [%(id)s].%(ext)s'),
    ];

    // Helper: check path is a real file (not a directory — Docker creates dirs for missing mounts)
    const isFile = (p) => { try { return fs.statSync(p).isFile(); } catch { return false; } };

    // Cookies: prefer --cookies-from-browser, fall back to --cookies file, auto-detect /app/cookies.txt
    const effectiveCookiesFile = cookiesFile || (isFile('/app/cookies.txt') ? '/app/cookies.txt' : '');
    if (cookiesFromBrowser) {
      args.push('--cookies-from-browser', cookiesFromBrowser);
    } else if (effectiveCookiesFile && isFile(effectiveCookiesFile)) {
      args.push('--cookies', effectiveCookiesFile);
    }

    if (disguise) {
      args.unshift('--add-header', `User-Agent:${userAgent}`);
      args.push('--sleep-requests', String(sleepMin));
      args.push('--sleep-interval', String(sleepMax));
    }

    args.push('-a', tempFile);

    console.log('[download] starting yt-dlp with', urls.length, 'URLs');

    ytdlpProcess = spawn(YTDLP, args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    let currentIndex = 0;
    let currentFilename = '';
    let completedCount = 0;
    let errorCount = 0;
    let fatalError = null;
    const results = [];

    ws.send(JSON.stringify({ type: 'started', totalVideos: urls.length }));

    function processLine(line) {
      line = line.trim();
      if (!line) return;

      if (line.startsWith('ERROR:') || line.includes(': error:')) {
        errorCount++;
        fatalError = fatalError || line;
        results.push({
          index: currentIndex || urls.length,
          url: urls[currentIndex] || urls[0] || '',
          status: 'failed',
          message: line,
        });
        ws.send(JSON.stringify({
          type: 'video_error',
          index: currentIndex || 1,
          total: urls.length,
          message: line,
          url: urls[currentIndex] || urls[0] || '',
          fatal: !currentIndex,
        }));
        return;
      }

      const destMatch = line.match(DEST_RE);
      if (destMatch) {
        currentFilename = path.basename(destMatch[1]);
        currentIndex++;
        ws.send(JSON.stringify({
          type: 'processing',
          index: currentIndex,
          total: urls.length,
          filename: currentFilename,
          url: urls[currentIndex - 1] || '',
        }));
        return;
      }

      const prog = parseProgress(line);
      if (prog) {
        ws.send(JSON.stringify({
          type: 'progress',
          index: currentIndex,
          total: urls.length,
          ...prog,
        }));
        return;
      }

      if (line.includes('[download] 100%')) {
        completedCount++;
        results.push({ index: currentIndex, filename: currentFilename, status: 'completed' });
        ws.send(JSON.stringify({
          type: 'completed',
          index: currentIndex,
          total: urls.length,
          completed: completedCount,
          failed: errorCount,
          filename: currentFilename,
        }));
        return;
      }

      if (line.includes('WARNING:')) {
        ws.send(JSON.stringify({
          type: 'log',
          level: 'warn',
          message: line,
        }));
        return;
      }
    }

    // Listen to stderr (progress + errors)
    let stderrBuf = '';
    ytdlpProcess.stderr.on('data', (chunk) => {
      stderrBuf += chunk.toString();
      const lines = stderrBuf.split('\n');
      stderrBuf = lines.pop();
      for (const line of lines) { try { processLine(line); } catch {} }
    });

    // Also listen to stdout
    let stdoutBuf = '';
    ytdlpProcess.stdout.on('data', (chunk) => {
      stdoutBuf += chunk.toString();
      const lines = stdoutBuf.split('\n');
      stdoutBuf = lines.pop();
      for (const line of lines) { try { processLine(line); } catch {} }
    });

    // Process exit
    ytdlpProcess.on('close', (code) => {
      if (stderrBuf.trim()) { try { processLine(stderrBuf); } catch {} }
      if (stdoutBuf.trim()) { try { processLine(stdoutBuf); } catch {} }
      cleanupTemp();
      ytdlpProcess = null;

      ws.send(JSON.stringify({
        type: 'batch_done',
        total: urls.length,
        succeeded: completedCount,
        failed: errorCount,
        exitCode: code,
        cancelled: cancelled,
        fatalError: fatalError,
        results: results,
      }));

      console.log(`[download] batch done: ${completedCount} ok, ${errorCount} failed, exit=${code}`);
    });

    ytdlpProcess.on('error', (err) => {
      cleanupTemp();
      ws.send(JSON.stringify({
        type: 'error',
        message: `Failed to start yt-dlp: ${err.message}`,
      }));
    });
  }
});

// ── Start Server ────────────────────────────────────────────────────
server.listen(PORT, () => {
  console.log(`\n  TikTok Downloader GUI`);
  console.log(`  Server running at: http://localhost:${PORT}`);
  console.log(`  Output directory:  ${DEFAULT_OUTPUT}\n`);
});
