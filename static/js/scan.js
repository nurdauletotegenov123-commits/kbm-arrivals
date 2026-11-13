// Сканер QR-пропусков для охраны. Код пропуска — ссылка /p/<код>/ (человек) или /g/<код>/ (бригада).
// Открываем тот же путь на этом сайте: так QR, напечатанный с другим адресом сайта (IP/домен), тоже сработает.
(function () {
  "use strict";
  const t = window.SCAN;
  const video = document.getElementById("scan-video");
  const view = document.getElementById("scan-view");
  const idle = document.getElementById("scan-idle");
  const status = document.getElementById("scan-status");
  const canvas = document.getElementById("scan-canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const PASS = /^\/(p|g)\/[A-Za-z0-9_-]+\/?$/;
  const detector = "BarcodeDetector" in window ? new window.BarcodeDetector({ formats: ["qr_code"] }) : null;
  let stream = null, busy = false, done = false, lastBad = 0;

  function say(text, kind) {
    status.textContent = text;
    status.className = "scan-status" + (kind ? " " + kind : "");
  }

  function handle(text) {
    let url;
    try { url = new URL(text, location.href); } catch (e) { url = null; }
    if (url && PASS.test(url.pathname)) {
      done = true;
      view.classList.add("ok");
      say(t.found, "ok");
      if (navigator.vibrate) navigator.vibrate(120);
      location.href = url.pathname.replace(/\/?$/, "/");
      return true;
    }
    if (Date.now() - lastBad > 1500) { say(t.notPass, "bad"); lastBad = Date.now(); }
    return false;
  }

  async function decode(source, w, h) {
    if (detector) {
      try {
        const codes = await detector.detect(source);
        if (codes.length) return codes[0].rawValue;
      } catch (e) { /* jsQR ниже */ }
    }
    const scale = Math.min(1, 960 / Math.max(w, h));
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(h * scale);
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
    const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const code = window.jsQR(img.data, img.width, img.height, { inversionAttempts: "attemptBoth" });
    return code ? code.data : null;
  }

  async function tick() {
    if (done || !stream) return;
    if (!busy && video.readyState >= 2) {
      busy = true;
      const text = await decode(video, video.videoWidth, video.videoHeight);
      busy = false;
      if (text && handle(text)) return;
    }
    requestAnimationFrame(tick);
  }

  async function start() {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      say(t.noCamera, "bad");
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
    } catch (e) {
      say(t.denied, "bad");
      return;
    }
    video.srcObject = stream;
    await video.play();
    view.classList.add("live");
    idle.classList.add("hidden");
    say(t.aim);
    try { localStorage.setItem("scan-camera", "1"); } catch (e) { /* приватный режим */ }
    requestAnimationFrame(tick);
  }

  document.getElementById("scan-start").addEventListener("click", start);

  document.getElementById("scan-photo").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const img = new Image();
    img.onload = async () => {
      const text = await decode(img, img.naturalWidth, img.naturalHeight);
      URL.revokeObjectURL(img.src);
      if (!text) say(t.noCode, "bad");
      else handle(text);
    };
    img.src = URL.createObjectURL(file);
  });

  // камеру уже разрешали — включаем сразу, без лишнего нажатия
  let allowed = false;
  try { allowed = localStorage.getItem("scan-camera") === "1"; } catch (e) { /* ничего */ }
  if (!window.isSecureContext) say(t.noCamera, "bad");
  else if (allowed) start();

  window.addEventListener("pagehide", () => { if (stream) stream.getTracks().forEach((tr) => tr.stop()); });
})();
