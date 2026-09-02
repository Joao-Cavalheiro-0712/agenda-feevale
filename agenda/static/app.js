/* Camada de interação do PWA. Nenhuma regra de negócio mora aqui:
   o cliente só chama a API e reflete o que o núcleo respondeu. */
(function () {
  "use strict";

  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || "";

  async function api(path, options = {}) {
    const opts = Object.assign({ headers: {} }, options);
    opts.headers["X-CSRF-Token"] = csrf();
    if (opts.json !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    const response = await fetch(path, opts);
    const text = await response.text();
    try { return JSON.parse(text); } catch { return { status: "FAILED", message: text }; }
  }
  window.api = api;

  /* ---------------- Tema ---------------- */
  const savedTheme = localStorage.getItem("theme");
  if (savedTheme && savedTheme !== "system") {
    document.documentElement.dataset.theme = savedTheme;
  }
  window.setTheme = (value) => {
    localStorage.setItem("theme", value);
    if (value === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = value;
  };

  /* ---------------- Toast com desfazer (SPEC §27) ---------------- */
  let toastTimer;
  function toast(message, actionId) {
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.innerHTML = "";
    const span = document.createElement("span");
    span.textContent = message;
    el.appendChild(span);
    if (actionId) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Desfazer";
      button.onclick = async () => {
        const result = await api(`/api/actions/${actionId}/undo`, { method: "POST" });
        toast(result.message || "Desfeito.");
        setTimeout(() => location.reload(), 600);
      };
      el.appendChild(button);
    }
    requestAnimationFrame(() => el.classList.add("show"));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), actionId ? 7000 : 3500);
  }
  window.toast = toast;

  /* ---------------- Bottom sheet ---------------- */
  function openSheet(id) {
    const sheet = document.getElementById(id);
    if (!sheet) return;
    document.querySelector(".sheet-backdrop")?.classList.add("open");
    sheet.classList.add("open");
    sheet.querySelector("input, textarea, button")?.focus({ preventScroll: true });
  }
  function closeSheets() {
    document.querySelectorAll(".sheet.open").forEach((s) => s.classList.remove("open"));
    document.querySelector(".sheet-backdrop")?.classList.remove("open");
  }
  window.openSheet = openSheet;
  window.closeSheets = closeSheets;

  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-sheet]");
    if (opener) { event.preventDefault(); openSheet(opener.dataset.sheet); return; }
    if (event.target.closest("[data-close-sheet]") || event.target.classList.contains("sheet-backdrop")) {
      closeSheets();
    }
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeSheets(); });

  /* ---------------- Concluir atividade ---------------- */
  document.addEventListener("click", async (event) => {
    const check = event.target.closest(".check[data-event-id]");
    if (!check) return;
    const done = check.getAttribute("aria-pressed") !== "true";
    check.setAttribute("aria-pressed", String(done));       // atualização otimista
    check.closest(".item")?.classList.toggle("done", done);
    const result = await api(`/api/events/${check.dataset.eventId}/complete`, {
      method: "POST", json: { done },
    });
    if (result.status !== "EXECUTED") {
      check.setAttribute("aria-pressed", String(!done));
      check.closest(".item")?.classList.toggle("done", !done);
      toast(result.message || "Não consegui salvar.");
    } else if (done) {
      toast("Concluído ✓", result.action_id);
    }
  });

  /* ---------------- Captura rápida ---------------- */
  async function sendCapture(payload) {
    const status = document.querySelector("[data-capture-status]");
    if (status) status.textContent = "Organizando…";
    const result = await api("/api/capture", payload);
    renderCaptureResult(result);
    return result;
  }
  window.sendCapture = sendCapture;

  function renderCaptureResult(result) {
    const status = document.querySelector("[data-capture-status]");
    const box = document.querySelector("[data-capture-result]");
    if (status) status.textContent = "";
    if (!box) { toast(result.message || "Pronto."); return; }

    box.innerHTML = "";
    if (result.transcript) {
      const heard = document.createElement("p");
      heard.className = "tiny muted";
      heard.textContent = `Ouvi: “${result.transcript}”`;
      box.appendChild(heard);
    }
    const message = document.createElement("p");
    message.className = "bubble bot";
    message.textContent = result.message || "Pronto.";
    box.appendChild(message);

    (result.cards || []).forEach((card) => box.appendChild(cardNode(card)));

    if (result.status === "NEEDS_CONFIRMATION" && result.action_id) {
      const actions = document.createElement("div");
      actions.className = "row";
      actions.style.marginTop = "10px";
      const yes = document.createElement("button");
      yes.className = "btn sm"; yes.type = "button"; yes.textContent = "Confirmar";
      yes.onclick = async () => {
        const confirmed = await api(`/api/actions/${result.action_id}/confirm`, { method: "POST" });
        renderCaptureResult(confirmed);
        refreshSoon();
      };
      const no = document.createElement("button");
      no.className = "btn sm ghost"; no.type = "button"; no.textContent = "Cancelar";
      no.onclick = async () => {
        await api(`/api/actions/${result.action_id}/reject`, { method: "POST" });
        box.innerHTML = "<p class='tiny muted'>Ok, não fiz nada.</p>";
      };
      actions.append(yes, no);
      box.appendChild(actions);
    }

    if (result.status === "EXECUTED") {
      if (result.undoable && result.action_id) toast(result.message || "Pronto.", result.action_id);
      refreshSoon();
    }
    if (result.redirect) setTimeout(() => (location.href = result.redirect), 900);
  }

  function cardNode(card) {
    const el = document.createElement("div");
    el.className = `item c-${card.color || "slate"}`;
    const rail = document.createElement("div"); rail.className = "rail";
    const time = document.createElement("div");
    time.className = "time" + (card.time ? "" : " empty");
    time.textContent = card.time || "—";
    const body = document.createElement("div"); body.className = "body";
    const title = document.createElement("div"); title.className = "title"; title.textContent = card.title || "";
    const meta = document.createElement("div"); meta.className = "meta";
    [card.type_label, card.date_label || card.when, card.subject, card.location]
      .filter(Boolean)
      .forEach((text, index) => {
        const span = document.createElement("span");
        span.textContent = index === 0 ? text : `· ${text}`;
        meta.appendChild(span);
      });
    body.append(title, meta);
    el.append(rail, time, body);
    return el;
  }
  window.cardNode = cardNode;

  let refreshTimer;
  function refreshSoon() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => {
      if (document.body.dataset.autorefresh !== "off") location.reload();
    }, 1200);
  }

  /* ---------------- Gravação de áudio ---------------- */
  let recorder = null;
  let chunks = [];
  async function toggleRecording(button) {
    if (recorder && recorder.state === "recording") { recorder.stop(); return; }
    if (!navigator.mediaDevices?.getUserMedia) { toast("Seu navegador não permite gravar áudio."); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recorder = new MediaRecorder(stream);
      chunks = [];
      recorder.ondataavailable = (event) => chunks.push(event.data);
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        button.classList.remove("recording");
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        if (blob.size < 1200) { toast("Áudio muito curto."); return; }
        const form = new FormData();
        form.append("audio", blob, "captura.webm");
        await sendCapture({ method: "POST", body: form });
      };
      recorder.start();
      button.classList.add("recording");
      toast("Gravando… toque de novo para enviar.");
    } catch {
      toast("Não consegui acessar o microfone.");
    }
  }
  window.toggleRecording = toggleRecording;

  /* ---------------- Formulários de captura ---------------- */
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-capture-form]");
    if (!form) return;
    event.preventDefault();
    const field = form.querySelector("[name=text]");
    const text = (field?.value || "").trim();
    if (!text) return;
    const box = document.querySelector("[data-capture-result]");
    if (box) {
      const mine = document.createElement("p");
      mine.className = "bubble user";
      mine.textContent = text;
      box.appendChild(mine);
    }
    field.value = "";
    await sendCapture({ method: "POST", json: { text } });
  });

  document.addEventListener("change", async (event) => {
    const input = event.target.closest("[data-capture-file]");
    if (!input || !input.files?.length) return;
    const form = new FormData();
    form.append("file", input.files[0]);
    toast("Enviando arquivo…");
    await sendCapture({ method: "POST", body: form });
    input.value = "";
  });

  /* ---------------- Progresso de documento (SPEC §90) ---------------- */
  const pending = document.querySelector("[data-document-poll]");
  if (pending) {
    const id = pending.dataset.documentPoll;
    const poll = setInterval(async () => {
      const data = await api(`/api/documents/${id}/status`);
      (data.progress || []).forEach((step, index) => {
        const node = pending.querySelector(`[data-step="${step.key}"]`);
        if (!node) return;
        node.classList.toggle("done", !!step.done);
        node.classList.toggle("active", !step.done && (data.progress[index - 1]?.done ?? true));
      });
      if (["READY", "NEEDS_REVIEW", "IMPORTED", "FAILED"].includes(data.status)) {
        clearInterval(poll);
        location.reload();
      }
    }, 1500);
  }

  /* ---------------- Barra superior ---------------- */
  const topbar = document.querySelector(".topbar");
  if (topbar) {
    const onScroll = () => topbar.classList.toggle("scrolled", window.scrollY > 4);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  /* ---------------- Service worker ---------------- */
  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
})();
