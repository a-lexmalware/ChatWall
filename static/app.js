/* =========================================================================
   PromptGuard — app.js
   Sin frameworks. Habla con la API FastAPI vía fetch (mismo origen, ya que
   el backend sirve este frontend como estáticos).
   ========================================================================= */

(() => {
  "use strict";

  const API_BASE = "/api"; // las rutas viven bajo /api/* en Vercel

  const els = {
    form: document.getElementById("analyzeForm"),
    promptInput: document.getElementById("promptInput"),
    forwardCheckbox: document.getElementById("forwardCheckbox"),
    analyzeBtn: document.getElementById("analyzeBtn"),
    resultArea: document.getElementById("resultArea"),

    gaugeFill: document.getElementById("gaugeFill"),
    gaugeNeedle: document.getElementById("gaugeNeedle"),
    gaugeScore: document.getElementById("gaugeScore"),
    verdictBadge: document.getElementById("verdictBadge"),
    verdictCaption: document.getElementById("verdictCaption"),

    reasonsList: document.getElementById("reasonsList"),

    claudeCard: document.getElementById("claudeCard"),
    claudeMeta: document.getElementById("claudeMeta"),
    claudeText: document.getElementById("claudeText"),

    statTotal: document.getElementById("statTotal"),
    statBlocked: document.getElementById("statBlocked"),
    statAllowed: document.getElementById("statAllowed"),

    barLow: document.getElementById("barLow"),
    barMedium: document.getElementById("barMedium"),
    barHigh: document.getElementById("barHigh"),
    countLow: document.getElementById("countLow"),
    countMedium: document.getElementById("countMedium"),
    countHigh: document.getElementById("countHigh"),

    historyBody: document.getElementById("historyBody"),

    apiDot: document.getElementById("apiDot"),
    apiStatusText: document.getElementById("apiStatusText"),
  };

  const GAUGE_ARC_LENGTH = 283; // ~ longitud del arco semicircular (r=90)

  const CATEGORY_LABELS = {
    prompt_injection: "Inyección de prompt",
    jailbreak: "Jailbreak",
    exfiltration: "Exfiltración",
    harmful_content: "Contenido dañino",
    obfuscation: "Ofuscación",
    social_engineering: "Ingeniería social",
  };

  function levelInfo(level) {
    if (level === "alto") return { cls: "high", label: "Riesgo alto" };
    if (level === "medio") return { cls: "medium", label: "Riesgo medio" };
    return { cls: "low", label: "Riesgo bajo" };
  }

  function setGauge(score) {
    const clamped = Math.max(0, Math.min(100, score));
    const offset = GAUGE_ARC_LENGTH * (1 - clamped / 100);
    els.gaugeFill.style.strokeDashoffset = String(offset);

    const deg = (clamped / 100) * 180 - 90;
    els.gaugeNeedle.style.transform = `rotate(${deg}deg)`;
    els.gaugeScore.textContent = String(clamped);

    const level = clamped >= 50 ? "alto" : clamped >= 20 ? "medio" : "bajo";
    const color = level === "alto" ? "var(--danger)" : level === "medio" ? "var(--warn)" : "var(--accent)";
    els.gaugeFill.style.stroke = color;
  }

  function renderVerdict(verdict) {
    const { cls, label } = levelInfo(verdict.risk_level);
    els.verdictBadge.textContent = `${label} · ${verdict.risk_score}/100`;
    els.verdictBadge.className = `badge badge--${cls}`;

    els.verdictCaption.textContent = verdict.blocked
      ? "Alerta bloqueada: este prompt no se reenvió al modelo."
      : "El prompt pasó el filtro y pudo reenviarse al modelo.";
  }

  function renderReasons(matches) {
    els.reasonsList.innerHTML = "";

    if (!matches || matches.length === 0) {
      const li = document.createElement("li");
      li.className = "reasons-list__empty";
      li.textContent = "No se dispararon reglas para este prompt.";
      els.reasonsList.appendChild(li);
      return;
    }

    for (const match of matches) {
      const li = document.createElement("li");
      li.className = "reason-item";

      const catSpan = document.createElement("span");
      catSpan.className = "reason-item__category";
      catSpan.style.color = "var(--text)";
      catSpan.style.background = "var(--panel)";
      catSpan.textContent = CATEGORY_LABELS[match.category] || match.category;

      const body = document.createElement("div");
      body.className = "reason-item__body";

      const desc = document.createElement("span");
      desc.className = "reason-item__desc";
      desc.textContent = match.description;

      const snippet = document.createElement("span");
      snippet.className = "reason-item__snippet";
      snippet.textContent = `"${match.snippet}"`;

      body.appendChild(desc);
      body.appendChild(snippet);

      const weight = document.createElement("span");
      weight.className = "reason-item__weight";
      weight.textContent = `+${match.weight}`;

      li.appendChild(catSpan);
      li.appendChild(body);
      li.appendChild(weight);

      els.reasonsList.appendChild(li);
    }
  }

  function renderClaude(claude) {
    if (!claude) {
      els.claudeCard.hidden = true;
      return;
    }
    els.claudeCard.hidden = false;
    els.claudeMeta.textContent = claude.stub
      ? `modelo: ${claude.model} · modo stub (sin API key configurada)`
      : `modelo: ${claude.model}`;
    els.claudeText.textContent = claude.text;
  }

  function formatTime(isoString) {
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch {
      return isoString;
    }
  }

  function truncate(text, n = 60) {
    if (text.length <= n) return text;
    return text.slice(0, n) + "…";
  }

  function renderHistory(items) {
    els.historyBody.innerHTML = "";

    if (!items || items.length === 0) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td colspan="5" class="history-table__empty">Sin análisis todavía.</td>`;
      els.historyBody.appendChild(tr);
      return;
    }

    for (const item of items) {
      const { cls, label } = levelInfo(item.risk_level);
      const tr = document.createElement("tr");

      const tdTime = document.createElement("td");
      tdTime.textContent = formatTime(item.created_at);

      const tdPrompt = document.createElement("td");
      const promptSpan = document.createElement("span");
      promptSpan.className = "history-table__prompt";
      promptSpan.title = item.prompt;
      promptSpan.textContent = truncate(item.prompt, 50);
      tdPrompt.appendChild(promptSpan);

      const tdScore = document.createElement("td");
      tdScore.textContent = item.risk_score;

      const tdLevel = document.createElement("td");
      const levelPill = document.createElement("span");
      levelPill.className = `pill pill--${cls}`;
      levelPill.textContent = label.replace("Riesgo ", "");
      tdLevel.appendChild(levelPill);

      const tdStatus = document.createElement("td");
      const statusPill = document.createElement("span");
      statusPill.className = item.blocked ? "pill pill--blocked" : "pill pill--allowed";
      statusPill.textContent = item.blocked ? "Bloqueado" : "Permitido";
      tdStatus.appendChild(statusPill);

      tr.appendChild(tdTime);
      tr.appendChild(tdPrompt);
      tr.appendChild(tdScore);
      tr.appendChild(tdLevel);
      tr.appendChild(tdStatus);

      els.historyBody.appendChild(tr);
    }
  }

  function renderStats(stats) {
    els.statTotal.textContent = stats.total;
    els.statBlocked.textContent = stats.blocked;
    els.statAllowed.textContent = stats.allowed;

    const byLevel = stats.by_risk_level || { bajo: 0, medio: 0, alto: 0 };
    const max = Math.max(byLevel.bajo, byLevel.medio, byLevel.alto, 1);

    els.barLow.style.width = `${(byLevel.bajo / max) * 100}%`;
    els.barMedium.style.width = `${(byLevel.medio / max) * 100}%`;
    els.barHigh.style.width = `${(byLevel.alto / max) * 100}%`;

    els.countLow.textContent = byLevel.bajo;
    els.countMedium.textContent = byLevel.medio;
    els.countHigh.textContent = byLevel.alto;
  }

  async function refreshSidebar() {
    try {
      const [historyRes, statsRes] = await Promise.all([
        fetch(`${API_BASE}/history?limit=15`),
        fetch(`${API_BASE}/stats`),
      ]);
      const history = await historyRes.json();
      const stats = await statsRes.json();
      renderHistory(history.items);
      renderStats(stats);
      setApiStatus(true);
    } catch (err) {
      setApiStatus(false);
    }
  }

  function setApiStatus(ok) {
    els.apiDot.className = `dot ${ok ? "dot--ok" : "dot--down"}`;
    els.apiStatusText.textContent = ok
      ? "API conectada"
      : "No se pudo conectar con la API — ¿está corriendo uvicorn?";
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const prompt = els.promptInput.value.trim();
    if (!prompt) return;

    els.analyzeBtn.disabled = true;
    els.analyzeBtn.textContent = "Analizando…";

    try {
      const res = await fetch(`${API_BASE}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt,
          forward: els.forwardCheckbox.checked,
        }),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const data = await res.json();

      els.resultArea.hidden = false;
      setGauge(data.verdict.risk_score);
      renderVerdict(data.verdict);
      renderReasons(data.verdict.matches);
      renderClaude(data.claude);

      setApiStatus(true);
      await refreshSidebar();
    } catch (err) {
      setApiStatus(false);
      els.resultArea.hidden = false;
      els.verdictCaption.textContent = "Error al contactar la API. Revisa que el backend esté corriendo.";
    } finally {
      els.analyzeBtn.disabled = false;
      els.analyzeBtn.textContent = "Analizar prompt";
    }
  }

  els.form.addEventListener("submit", handleSubmit);

  refreshSidebar();
})();
