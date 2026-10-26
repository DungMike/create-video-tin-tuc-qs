// Mot lenh `npm run dev` bat ca 3 app cua luong video:
//   svs    Story Video Studio (repo nay): Flask + Vite, chay trong terminal nay, Ctrl+C la tat.
//   tts    App giong doc DGT (DGT_APP_DIR, mac dinh ../app-tts-dgt): DgtAutoTTSMM.exe + Automation API.
//   image  App anh TST (TST_APP_DIR, mac dinh ../app-tst-studio): app TST kem bridge + TST Studio.
// App giong / anh dung chung voi kenh khac nen chi duoc BAT khi chua chay va KHONG tat khi Ctrl+C.
// Chung chay an, log ghi vao logs/services/ va duoc in lai trong terminal nay.
// App TST doi quyen admin: dang mo ma khong co API bridge (vd vua tu cap nhat) thi tu mo lai kem API qua Scheduled Task
// quyen cao nhat cua tst-bridge\start-with-api.ps1 (lan dau bam Yes o UAC 1 lan).
//
//   npm run dev                       bat tat ca
//   npm run dev -- --skip=tts,image   bo qua nhom (svs, tts, image); dat DEV_SKIP trong .env de nho
//   npm run dev -- --no-desktop       khong tu mo app desktop DGT / TST, chi bao trang thai
//   npm run dev:status                xem trang thai
//   npm run dev:stop                  tat API DGT + TST Studio khi dang ranh (--force: tat ca khi co job)
// Trong luc chay go `s` + Enter: trang thai, `o`: mo trang web, `q`: thoat.

import { execFile, spawn } from "node:child_process";
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, readSync, rmSync, statSync } from "node:fs";
import net from "node:net";
import { join, resolve } from "node:path";
import readline from "node:readline";
import { StringDecoder } from "node:string_decoder";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileP = promisify(execFile);
const ROOT = fileURLToPath(new URL("..", import.meta.url));

// ---------------------------------------------------------------- cau hinh

// .env giong python-dotenv / vite: bien da co trong process.env thang.
function readEnvFile(file) {
  const out = {};
  if (!existsSync(file)) return out;
  for (const raw of readFileSync(file, "utf8").split(/\r?\n/)) {
    const m = raw.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!m) continue;
    let value = m[2].trim();
    if (/^(["']).*\1$/.test(value)) value = value.slice(1, -1);
    else value = value.replace(/\s+#.*$/, "");
    out[m[1]] = value;
  }
  return out;
}

const ENV = { ...readEnvFile(join(ROOT, ".env")), ...process.env };
const trimUrl = (value, fallback) => (value || fallback).replace(/\/+$/, "");
const urlPort = (url) => Number(new URL(url).port || 80);

const CFG = {
  webHost: ENV.WEB_HOST || "127.0.0.1",
  webPort: Number(ENV.WEB_PORT || 5000),
  frontHost: ENV.FRONTEND_HOST || "127.0.0.1",
  frontPort: Number(ENV.FRONTEND_PORT || 5173),
  dgtUrl: trimUrl(ENV.DGT_API_URL, "http://127.0.0.1:8787"),
  studioUrl: trimUrl(ENV.TST_STUDIO_URL, "http://127.0.0.1:5180"),
  dgtDir: resolve(ROOT, ENV.DGT_APP_DIR || "../app-tts-dgt"),
  tstDir: resolve(ROOT, ENV.TST_APP_DIR || "../app-tst-studio"),
  logDir: join(ROOT, "logs", "services"),
};

// Cong + API key cua TST bridge lay tu bridge.config.json (co dong comment //), giong start-with-api.ps1.
function readBridgeConfig() {
  let cfg = {};
  try {
    const text = readFileSync(join(CFG.tstDir, "tst-bridge", "bin", "bridge.config.json"), "utf8");
    cfg = JSON.parse(text.replace(/^﻿/, "").replace(/^\s*\/\/.*$/gm, ""));
  } catch {
    // khong co file: dung mac dinh cua bridge
  }
  return { url: trimUrl(process.env.TST_API, `http://127.0.0.1:${cfg.port || 5179}/api`), apiKey: process.env.TST_API_KEY || cfg.apiKey || "" };
}
const BRIDGE = readBridgeConfig();

const LOG = {
  ttsLaunch: join(CFG.logDir, "tts-launch.log"),
  ttsApi: join(CFG.logDir, "tts-api.log"),
  ttsApiErr: join(CFG.logDir, "tts-api.err.log"),
  studio: join(CFG.logDir, "tst-studio.log"),
  studioErr: join(CFG.logDir, "tst-studio.err.log"),
};

const argv = process.argv.slice(2);
const COMMAND = argv.find((a) => !a.startsWith("-")) || "start";
const option = (name) => argv.find((a) => a === `--${name}` || a.startsWith(`--${name}=`));
const GROUPS = ["svs", "tts", "image"];
const SKIP = new Set(
  (option("skip")?.split("=")[1] ?? ENV.DEV_SKIP ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean),
);
const NO_DESKTOP = Boolean(option("no-desktop"));
const FORCE = Boolean(option("force"));

// ---------------------------------------------------------------- in log

const COLOR = process.stdout.isTTY && !process.env.NO_COLOR;
const paint = (code, text) => (COLOR ? `\x1b[${code}m${text}\x1b[0m` : text);
const TAG_COLOR = { dev: "1", "svs-api": "36", "svs-web": "34", tts: "35", "tts-api": "35", tst: "33", "tst-studio": "33" };

function say(tag, text) {
  const label = paint(TAG_COLOR[tag] || "37", `[${tag}]`.padEnd(12));
  for (const line of String(text).split(/\r?\n/)) console.log(`${label} ${line}`);
}

function lineWriter(tag) {
  const decoder = new StringDecoder("utf8");
  let rest = "";
  const emit = (line) => {
    const clean = line.replace(/^﻿/, "").trimEnd();
    if (clean.trim()) say(tag, clean);
  };
  const write = (chunk) => {
    rest += decoder.write(chunk);
    const lines = rest.split(/\r?\n/);
    rest = lines.pop();
    lines.forEach(emit);
  };
  write.end = () => {
    emit(rest + decoder.end());
    rest = "";
  };
  return write;
}

function pipeLines(tag, stream) {
  const write = lineWriter(tag);
  stream.on("data", write);
  stream.on("end", () => write.end());
}

// In phan moi cua mot file log (dich vu chay an ghi log ra file).
const timers = [];
const tailed = new Set();
function tail(tag, file, { fromStart = false } = {}) {
  if (tailed.has(file)) return;
  tailed.add(file);
  let pos = !fromStart && existsSync(file) ? statSync(file).size : 0;
  const write = lineWriter(tag);
  timers.push(
    setInterval(() => {
      let size;
      try {
        size = statSync(file).size;
      } catch {
        return;
      }
      if (size < pos) pos = 0;
      if (size === pos) return;
      let fd;
      try {
        fd = openSync(file, "r");
        const buf = Buffer.alloc(size - pos);
        const n = readSync(fd, buf, 0, buf.length, pos);
        pos += n;
        write(buf.subarray(0, n));
      } catch {
        // file dang bi ghi do, lan sau doc tiep
      } finally {
        if (fd !== undefined) closeSync(fd);
      }
    }, 600),
  );
}

function removeFile(file) {
  try {
    rmSync(file, { force: true });
  } catch {
    // file con bi giu: Start-Process se bao loi neu khong ghi duoc
  }
}

// ---------------------------------------------------------------- tien trinh

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const children = new Set();
let stopping = false;

function startChild(tag, command, args, options = {}) {
  const child = spawn(command, args, {
    cwd: options.cwd || ROOT,
    env: options.env || process.env,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  pipeLines(tag, child.stdout);
  pipeLines(tag, child.stderr);
  children.add(child);
  child.once("error", (error) => say(tag, `Không chạy được ${command}: ${error.message}`));
  child.once("exit", (code, signal) => {
    children.delete(child);
    if (!stopping) say(tag, `Đã dừng (${signal ?? `mã ${code}`}). Gõ q để thoát rồi chạy lại npm run dev.`);
  });
  return child;
}

function runOnce(tag, command, args, options = {}) {
  return new Promise((done) => {
    const child = spawn(command, args, {
      cwd: options.cwd || ROOT,
      env: options.env || process.env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    pipeLines(tag, child.stdout);
    pipeLines(tag, child.stderr);
    child.once("error", (error) => {
      say(tag, `Không chạy được ${command}: ${error.message}`);
      done(-1);
    });
    // Cho "exit" chu khong cho "close": tien trinh do Start-Process bat ra ke thua ong stdout nay
    // va song lau hon, nen ong khong bao gio dong. Doi them chut cho dong log cuoi kip in.
    child.once("exit", (code) => setTimeout(() => done(code ?? -1), 300));
  });
}

const psQuote = (s) => `'${String(s).replace(/'/g, "''")}'`;
const POWERSHELL = ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"];

// Dich vu dung chung song lau hon terminal nay: Start-Process cho no console an rieng,
// nen Ctrl+C o day khong cham toi no va script con (python) cua no khong bat cua so.
function startHidden(tag, { file, args, cwd, out, err, env }) {
  const command = [
    `Start-Process -FilePath ${psQuote(file)}`,
    `-ArgumentList ${args.map(psQuote).join(",")}`,
    `-WorkingDirectory ${psQuote(cwd)} -WindowStyle Hidden`,
    `-RedirectStandardOutput ${psQuote(out)} -RedirectStandardError ${psQuote(err)}`,
  ].join(" ");
  return runOnce(tag, "powershell.exe", [...POWERSHELL, "-Command", command], { env });
}

// Bien moi truong sach cho dich vu dung chung: bo bien npm_* va node_modules/.bin cua repo nay.
function sharedEnv(extra) {
  const env = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (!/^npm_/i.test(key) && key !== "INIT_CWD") env[key] = value;
  }
  const pathKey = Object.keys(env).find((k) => k.toLowerCase() === "path");
  if (pathKey) {
    env[pathKey] = env[pathKey]
      .split(";")
      .filter((p) => !/node_modules[\\/]\.bin|node-gyp-bin/i.test(p))
      .join(";");
  }
  return { ...env, ...extra };
}

async function waitUntil(check, timeoutMs, onNotice) {
  const deadline = Date.now() + timeoutMs;
  let notice = Date.now() + 30000;
  while (Date.now() < deadline && !stopping) {
    if (await check()) return true;
    if (onNotice && Date.now() >= notice) {
      onNotice();
      notice += 30000;
    }
    await sleep(1000);
  }
  return false;
}

async function killTree(pid) {
  try {
    await execFileP("taskkill.exe", ["/PID", String(pid), "/T", "/F"], { windowsHide: true });
    return true;
  } catch {
    return false;
  }
}

async function listenerPid(port) {
  try {
    const { stdout } = await execFileP(
      "powershell.exe",
      [...POWERSHELL, "-Command", `(Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess`],
      { windowsHide: true },
    );
    return Number(stdout.trim()) || null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- kiem tra dich vu

async function getJson(url, { timeout = 2500, headers } = {}) {
  try {
    const res = await fetch(url, { headers, signal: AbortSignal.timeout(timeout) });
    return { ok: res.ok, status: res.status, body: await res.json().catch(() => null) };
  } catch {
    return null;
  }
}

function portOpen(port, host = "127.0.0.1") {
  return new Promise((done) => {
    const socket = net.connect({ port, host });
    const finish = (value) => {
      socket.destroy();
      done(value);
    };
    socket.setTimeout(800);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
  });
}

async function processRunning(image) {
  try {
    const { stdout } = await execFileP("tasklist.exe", ["/FI", `IMAGENAME eq ${image}`, "/NH", "/FO", "CSV"], { windowsHide: true });
    return stdout.toLowerCase().includes(`"${image.toLowerCase()}"`);
  } catch {
    return false;
  }
}

const REASON_VI = {
  desktop_app_not_running: "app DGT chưa mở",
  desktop_busy: "đang tạo giọng",
  desktop_initializing: "đang khởi tạo",
  desktop_modal_open: "app DGT đang mở hộp thoại, đóng nó đi",
  desktop_multiple_instances: "đang mở nhiều cửa sổ DGT, chỉ giữ 1",
  desktop_not_responding: "app DGT không phản hồi",
  desktop_permission_mismatch: "API khác quyền Administrator với app DGT",
  desktop_window_closed: "cửa sổ DGT đã đóng",
};

const probePort = async (port) => ((await portOpen(port)) ? { state: "ok", text: "đang chạy" } : { state: "down", text: "chưa chạy" });

async function probeTtsApp() {
  return (await processRunning("DgtAutoTTSMM.exe")) ? { state: "ok", text: "đang mở" } : { state: "down", text: "chưa mở" };
}

async function probeTtsApi() {
  const health = await getJson(`${CFG.dgtUrl}/health`);
  if (!health) return { state: "down", text: "chưa chạy" };
  if (health.body?.service !== "dgt-automation-api") return { state: "warn", text: "cổng đang bị app khác chiếm", foreign: true };
  const runtime = (await getJson(`${CFG.dgtUrl}/api/v1/runtime`))?.body;
  const desktop = runtime?.adapters?.find((a) => a.adapter === "legacy-desktop");
  const job = runtime?.active_job_id ? " · đang đọc 1 file" : "";
  if (!desktop || desktop.ready || desktop.reason === "desktop_busy") return { state: "ok", text: `sẵn sàng${job}`, active: Boolean(job) };
  return { state: "warn", text: `chờ: ${REASON_VI[desktop.reason] || desktop.reason}${job}`, reason: desktop.reason, active: Boolean(job) };
}

async function probeTstApp() {
  const headers = BRIDGE.apiKey ? { "X-API-Key": BRIDGE.apiKey } : undefined;
  const health = await getJson(`${BRIDGE.url}/health`, { headers });
  if (health?.body) {
    return health.body.loggedIn ? { state: "ok", text: "đã đăng nhập" } : { state: "warn", text: "chưa đăng nhập license (bấm Đăng nhập trên app TST)" };
  }
  if (await processRunning("TSTAutoGenerateImage.exe")) return { state: "warn", text: "đang mở nhưng không có API bridge", noBridge: true };
  return { state: "down", text: "chưa mở" };
}

async function probeStudio() {
  const jobs = await getJson(`${CFG.studioUrl}/api/jobs`);
  if (Array.isArray(jobs?.body)) {
    const running = jobs.body.filter((j) => j.status === "running").length;
    return { state: "ok", text: running ? `đang chạy · ${running} job đang làm` : "đang chạy", running };
  }
  if (await portOpen(urlPort(CFG.studioUrl))) return { state: "warn", text: "cổng đang bị app khác chiếm", foreign: true };
  return { state: "down", text: "chưa chạy" };
}

const SERVICES = [
  { key: "svs-api", group: "svs", tag: "svs-api", name: "Story Video · API", where: `http://${CFG.webHost}:${CFG.webPort}`, probe: () => probePort(CFG.webPort) },
  { key: "svs-web", group: "svs", tag: "svs-web", name: "Story Video · web", where: `http://${CFG.frontHost}:${CFG.frontPort}`, probe: () => probePort(CFG.frontPort) },
  { key: "tts-app", group: "tts", tag: "tts", name: "Giọng đọc · app DGT", where: "DgtAutoTTSMM.exe", probe: probeTtsApp },
  { key: "tts-api", group: "tts", tag: "tts-api", name: "Giọng đọc · API + web", where: CFG.dgtUrl, probe: probeTtsApi },
  { key: "tst-app", group: "image", tag: "tst", name: "Ảnh · app TST", where: BRIDGE.url, probe: probeTstApp },
  { key: "tst-studio", group: "image", tag: "tst-studio", name: "Ảnh · TST Studio", where: CFG.studioUrl, probe: probeStudio },
];

async function snapshot() {
  return Object.fromEntries(await Promise.all(SERVICES.map(async (s) => [s.key, await s.probe()])));
}

function printStatus(snap) {
  const dot = { ok: paint("32", "●"), warn: paint("33", "●"), down: paint("31", "○") };
  console.log("");
  for (const s of SERVICES) {
    const p = snap[s.key];
    const skipped = SKIP.has(s.group) ? paint("2", " (bỏ qua)") : "";
    console.log(`  ${dot[p.state]} ${s.name.padEnd(23)} ${s.where.padEnd(27)} ${p.text}${skipped}`);
  }
  console.log("");
}

// ---------------------------------------------------------------- bat tung nhom

async function startSvs() {
  const python = join(ROOT, "venv", "Scripts", "python.exe");
  if (await portOpen(CFG.webPort)) {
    say("svs-api", `Cổng ${CFG.webPort} đã có server (Story Video đang chạy ở terminal khác?): dùng lại, không bật thêm.`);
  } else if (!existsSync(python)) {
    say("svs-api", "Chưa có venv. Chạy: python -m venv venv; .\\venv\\Scripts\\pip install -r requirements.txt");
  } else {
    // Pipe thay vi console nen phai ep utf-8 + khong dem, neu khong log tieng Viet loi / den tre.
    startChild("svs-api", python, ["-m", "src.web_app"], { env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" } });
  }

  const vite = join(ROOT, "frontend", "node_modules", "vite", "bin", "vite.js");
  if (await portOpen(CFG.frontPort)) {
    say("svs-web", `Cổng ${CFG.frontPort} đã có server: dùng lại, không bật thêm.`);
  } else if (!existsSync(vite)) {
    say("svs-web", "Chưa cài frontend. Chạy: npm --prefix frontend install");
  } else {
    startChild("svs-web", process.execPath, [vite, "--strictPort"], { cwd: join(ROOT, "frontend") });
  }
}

async function startTts() {
  if (!existsSync(join(CFG.dgtDir, "DgtAutoTTSMM.exe"))) {
    say("tts", `Không thấy app DGT ở ${CFG.dgtDir}. Đặt DGT_APP_DIR trong .env.`);
    return;
  }
  const port = urlPort(CFG.dgtUrl);
  const appUp = await probeTtsApp();
  const api = await probeTtsApi();
  const needApp = appUp.state !== "ok" && !NO_DESKTOP;
  let needApi = api.state === "down";

  if (appUp.state !== "ok" && NO_DESKTOP) say("tts", "App DGT chưa mở (--no-desktop: không tự mở).");
  if (api.foreign) say("tts", `Cổng ${port} đang bị app khác chiếm, không bật được API DGT.`);
  if (api.state !== "down" && !api.foreign) {
    say("tts", `API DGT đã chạy ở ${CFG.dgtUrl}: dùng lại.`);
    tail("tts-api", LOG.ttsApi);
    tail("tts-api", LOG.ttsApiErr);
  }
  if (needApi && !existsSync(join(CFG.dgtDir, ".tools", "dotnet", "dotnet.exe"))) {
    say("tts", `API DGT chưa setup. Chạy một lần: powershell -ExecutionPolicy Bypass -File "${join(CFG.dgtDir, "scripts", "Setup.ps1")}"`);
    needApi = false;
  }

  if (needApp || needApi) {
    if (needApi) {
      removeFile(LOG.ttsApi);
      removeFile(LOG.ttsApiErr);
      tail("tts-api", LOG.ttsApi, { fromStart: true });
      tail("tts-api", LOG.ttsApiErr, { fromStart: true });
    }
    tail("tts", LOG.ttsLaunch);
    const what = [needApp && "mở app DGT", needApi && "bật API"].filter(Boolean).join(" + ");
    say("tts", `${what}: app DGT chạy quyền Administrator nên Windows sẽ hỏi UAC, bấm Yes.`);
    const code = await runOnce("tts", "powershell.exe", [
      ...POWERSHELL,
      "-File",
      join(ROOT, "scripts", "dev-tts.ps1"),
      "-AppDir",
      CFG.dgtDir,
      "-LogDir",
      CFG.logDir,
      "-Port",
      String(port),
      ...(needApp ? [] : ["-NoDesktop"]),
      ...(needApi ? [] : ["-NoApi"]),
    ]);
    if (code === 3) {
      say("tts", "Không được cấp quyền Administrator: bỏ qua app giọng đọc. Chạy lại npm run dev khi cần.");
      return;
    }
    if (code !== 0) {
      say("tts", `Bật app giọng đọc lỗi (mã ${code}). Xem ${LOG.ttsLaunch}`);
      return;
    }
    if (needApi) {
      const ready = await waitUntil(
        async () => (await probeTtsApi()).state !== "down",
        180000,
        () => say("tts", "Đang chờ API DGT build + khởi động..."),
      );
      if (!ready) {
        say("tts", `API DGT chưa lên sau 3 phút. Xem ${LOG.ttsApi}`);
        return;
      }
    }
  }

  const now = await probeTtsApi();
  if (now.state === "down") return;
  say("tts", `API DGT: ${now.text}. Web giọng đọc: ${CFG.dgtUrl}/`);
  if (now.reason === "desktop_permission_mismatch") {
    say("tts", "API và app DGT khác quyền Administrator: npm run dev:stop rồi npm run dev lại để API chạy cùng quyền.");
  }
}

// App TST đòi quyền admin: start-with-api.ps1 -Restart nhờ Scheduled Task quyền cao nhất tắt / mở app kèm bridge
// (lần đầu tạo task cần bấm Yes ở UAC một lần). -Restart cũng mở app khi app chưa chạy.
async function startTstApp(message) {
  say("tst", message);
  const script = join(CFG.tstDir, "tst-bridge", "start-with-api.ps1");
  const code = await runOnce("tst", "powershell.exe", [
    ...POWERSHELL,
    "-Command",
    `try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}; & ${psQuote(script)} -Restart; exit $LASTEXITCODE`,
  ]);
  if (code !== 0) say("tst", `Mở app TST lỗi (mã ${code}), xem dòng phía trên.`);
  return code;
}

async function startImage() {
  if (!existsSync(join(CFG.tstDir, "studio"))) {
    say("tst", `Không thấy TST Studio ở ${CFG.tstDir}. Đặt TST_APP_DIR trong .env.`);
    return;
  }

  const app = await probeTstApp();
  if (app.state !== "down" && !app.noBridge) {
    say("tst", `App TST: ${app.text}`);
  } else if (NO_DESKTOP) {
    say("tst", app.noBridge ? "App TST đang mở nhưng không có API bridge (--no-desktop: không tự mở lại)." : "App TST chưa mở (--no-desktop: không tự mở).");
  } else {
    await startTstApp(app.noBridge ? "App TST đang mở nhưng không có API bridge: mở lại kèm API..." : "Mở app TST kèm API bridge...");
  }

  const studio = await probeStudio();
  if (studio.foreign) {
    say("tst-studio", `Cổng ${urlPort(CFG.studioUrl)} đang bị app khác chiếm, không bật được TST Studio.`);
    return;
  }
  if (studio.state === "ok") {
    say("tst-studio", `TST Studio đã chạy ở ${CFG.studioUrl}: dùng lại.`);
    tail("tst-studio", LOG.studio);
    tail("tst-studio", LOG.studioErr);
    return;
  }

  const studioDir = join(CFG.tstDir, "studio");
  if (!existsSync(join(studioDir, "node_modules", "express"))) {
    say("tst-studio", "Cài thư viện TST Studio lần đầu (npm install)...");
    const code = await runOnce("tst-studio", "cmd.exe", ["/d", "/s", "/c", "npm install --no-audit --no-fund"], { cwd: studioDir });
    if (code !== 0) {
      say("tst-studio", "Cài thư viện lỗi. Kiểm tra Node.js rồi chạy lại.");
      return;
    }
  }
  removeFile(LOG.studio);
  removeFile(LOG.studioErr);
  tail("tst-studio", LOG.studio, { fromStart: true });
  tail("tst-studio", LOG.studioErr, { fromStart: true });
  const env = sharedEnv({ PORT: String(urlPort(CFG.studioUrl)), TST_API: BRIDGE.url, ...(BRIDGE.apiKey ? { TST_API_KEY: BRIDGE.apiKey } : {}) });
  const code = await startHidden("tst-studio", { file: process.execPath, args: ["server/index.js"], cwd: studioDir, out: LOG.studio, err: LOG.studioErr, env });
  if (code !== 0) {
    say("tst-studio", `Không bật được TST Studio (mã ${code}).`);
    return;
  }
  if (!(await waitUntil(async () => (await probeStudio()).state === "ok", 60000))) {
    say("tst-studio", `TST Studio chưa lên sau 60 giây. Xem ${LOG.studioErr}`);
  }
}

// ---------------------------------------------------------------- lenh

function openUrl(url) {
  spawn("explorer.exe", [url], { detached: true, stdio: "ignore" }).unref();
}

function watch(initial) {
  let last = initial;
  let busy = false;
  // App TST tự cập nhật (Velopack) rồi tự mở lại KHÔNG có bridge: mở lại kèm API, tối đa 1 lần / 2 phút.
  let healing = false;
  let lastHeal = 0;
  timers.push(
    setInterval(async () => {
      if (busy || stopping) return;
      busy = true;
      try {
        const snap = await snapshot();
        for (const s of SERVICES) {
          if (!SKIP.has(s.group) && last[s.key].text !== snap[s.key].text) say(s.tag, `${s.name}: ${snap[s.key].text}`);
        }
        last = snap;
        if (snap["tst-app"].noBridge && !SKIP.has("image") && !NO_DESKTOP && !healing && Date.now() - lastHeal > 120000) {
          healing = true;
          lastHeal = Date.now();
          startTstApp("App TST vừa mở lại mà không có API (vd tự cập nhật): mở lại kèm API...").finally(() => {
            healing = false;
          });
        }
      } finally {
        busy = false;
      }
    }, 10000),
  );
}

function listenKeys() {
  const rl = readline.createInterface({ input: process.stdin });
  rl.on("line", async (line) => {
    const key = line.trim().toLowerCase();
    if (key === "s") printStatus(await snapshot());
    else if (key === "o") openUrl(`http://${CFG.frontHost}:${CFG.frontPort}/pipeline`);
    else if (key === "q") void shutdown(0);
    else if (key) say("dev", "Gõ s = trạng thái, o = mở trang web, q = thoát.");
  });
}

async function shutdown(code) {
  if (stopping) return;
  stopping = true;
  timers.forEach(clearInterval);
  const list = [...children];
  if (list.length) say("dev", "Đang tắt Story Video Studio...");
  await Promise.all(list.map((child) => (child.exitCode === null && child.pid ? killTree(child.pid) : null)));
  const shared = [!SKIP.has("tts") && "giọng đọc (DGT)", !SKIP.has("image") && "ảnh (TST)"].filter(Boolean);
  if (shared.length) say("dev", `Không tắt app ${shared.join(" và ")} vì dùng chung với kênh khác. Tắt API DGT + TST Studio: npm run dev:stop`);
  process.exit(code);
}

async function start() {
  const unknown = [...SKIP].filter((g) => !GROUPS.includes(g));
  if (unknown.length) {
    say("dev", `--skip chỉ nhận ${GROUPS.join(", ")} (gặp: ${unknown.join(", ")}).`);
    process.exit(1);
  }
  mkdirSync(CFG.logDir, { recursive: true });
  const on = [!SKIP.has("svs") && "Story Video Studio", !SKIP.has("tts") && "giọng đọc (DGT)", !SKIP.has("image") && "ảnh (TST)"].filter(Boolean);
  say("dev", `Bật ${on.join(" + ")}. Log dịch vụ nền: ${CFG.logDir}`);

  process.on("SIGINT", () => void shutdown(0));
  process.on("SIGTERM", () => void shutdown(0));
  process.on("SIGBREAK", () => void shutdown(0));

  if (!SKIP.has("svs")) await startSvs();
  const groups = [];
  if (!SKIP.has("tts")) groups.push(startTts().catch((e) => say("tts", `Lỗi: ${e.message}`)));
  if (!SKIP.has("image")) groups.push(startImage().catch((e) => say("tst", `Lỗi: ${e.message}`)));
  await Promise.all(groups);
  if (stopping) return;

  const snap = await snapshot();
  printStatus(snap);
  say("dev", `Mở ${paint("1", `http://${CFG.frontHost}:${CFG.frontPort}/pipeline`)}  ·  gõ s = trạng thái, o = mở trang web, q = thoát`);
  watch(snap);
  listenKeys();
}

async function stopShared() {
  const studio = await probeStudio();
  if (studio.state !== "ok") {
    say("tst-studio", "TST Studio không chạy.");
  } else if (studio.running && !FORCE) {
    say("tst-studio", `Đang có ${studio.running} job chạy, không tắt. Thêm --force để tắt luôn (job sẽ dừng giữa chừng).`);
  } else {
    const pid = await listenerPid(urlPort(CFG.studioUrl));
    say("tst-studio", pid && (await killTree(pid)) ? "Đã tắt TST Studio." : "Không tắt được TST Studio.");
  }

  const api = await probeTtsApi();
  if (api.state === "down" || api.foreign) {
    say("tts", "API DGT không chạy.");
  } else if (api.active) {
    say("tts", "API DGT đang đọc một file, không tắt. Đợi xong (hoặc huỷ trên web giọng đọc) rồi chạy lại.");
  } else {
    mkdirSync(CFG.logDir, { recursive: true });
    tail("tts", LOG.ttsLaunch);
    say("tts", "Tắt API DGT (API chạy quyền Administrator nên Windows sẽ hỏi UAC)...");
    const port = urlPort(CFG.dgtUrl);
    const code = await runOnce("tts", "powershell.exe", [
      ...POWERSHELL,
      "-File",
      join(ROOT, "scripts", "dev-tts.ps1"),
      "-AppDir",
      CFG.dgtDir,
      "-LogDir",
      CFG.logDir,
      "-Port",
      String(port),
      "-Action",
      "Stop",
    ]);
    const closed = code === 0 && (await waitUntil(async () => !(await portOpen(port)), 15000));
    await sleep(800);
    say("tts", closed ? "Đã tắt API DGT. Hàng đợi, voice và file đã xong vẫn giữ." : `Chưa tắt được API DGT (mã ${code}). Xem ${LOG.ttsLaunch}`);
  }
  say("dev", "App desktop DGT / TST không tự tắt: đóng cửa sổ app nếu muốn.");
  timers.forEach(clearInterval);
}

if (process.platform !== "win32") {
  console.error("scripts/dev.mjs chỉ chạy trên Windows (app DGT / TST là app Windows).");
  process.exit(1);
}

if (COMMAND === "start") {
  await start();
} else if (COMMAND === "status") {
  printStatus(await snapshot());
} else if (COMMAND === "stop") {
  await stopShared();
} else {
  console.error("Dùng: node scripts/dev.mjs [start|status|stop] [--skip=svs,tts,image] [--no-desktop] [--force]");
  process.exit(1);
}
