const state = {
  project: null,
  clips: [],
  config: null,
};

// --- utilidades ---------------------------------------------------------

function showToast(message, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), isError ? 4000 : 2200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    const message = Array.isArray(detail?.issues) ? detail.issues.join("; ") : (detail || "erro");
    throw new Error(message);
  }
  return body;
}

function qs(id) { return document.getElementById(id); }

// --- navegação -----------------------------------------------------------

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    qs(`view-${btn.dataset.view}`).classList.add("active");
  });
});

// --- projetos --------------------------------------------------------------

async function loadProjects() {
  const projects = await api("/api/projects");
  const select = qs("project-select");
  select.innerHTML = "";

  if (!projects.length) {
    select.innerHTML = "<option>Nenhum projeto encontrado</option>";
    return;
  }

  for (const project of projects) {
    const option = document.createElement("option");
    option.value = project.path;
    option.textContent = `${project.name} (${project.clip_count} clips)`;
    select.appendChild(option);
  }

  select.value = projects[0].path;
  state.project = projects[0].path;
  select.addEventListener("change", () => {
    state.project = select.value;
    loadClips();
    loadQueue();
  });
}

// --- clips -----------------------------------------------------------------

async function loadClips() {
  if (!state.project) return;
  state.clips = await api(`/api/projects/clips?path=${encodeURIComponent(state.project)}`);
  renderClipStats();
  renderClipGrid();
}

function renderClipStats() {
  const total = state.clips.length;
  const byStatus = {};
  for (const c of state.clips) byStatus[c.upload_status] = (byStatus[c.upload_status] || 0) + 1;
  const minutes = state.clips.reduce((sum, c) => sum + parseFloat(c.duracao || 0), 0) / 60;

  qs("clips-stats").innerHTML = [
    [total, "clips"],
    [(byStatus.publicado || 0), "publicados"],
    [(byStatus.agendado || 0), "agendados"],
    [(byStatus.pendente || 0), "pendentes"],
    [minutes.toFixed(0) + " min", "material"],
  ].map(([value, label]) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`).join("");
}

function renderClipGrid() {
  const filter = qs("clips-filter").value;
  const sort = qs("clips-sort").value;

  let list = state.clips.filter((c) => filter === "todos" || c.upload_status === filter);
  if (sort === "score") list = [...list].sort((a, b) => b.score - a.score);

  const grid = qs("clip-grid");
  if (!list.length) {
    grid.innerHTML = '<div class="empty-state">Nenhum clip com esse filtro.</div>';
    return;
  }

  grid.innerHTML = "";
  for (const clip of list) {
    const card = document.createElement("article");
    card.className = "clip-card";

    const thumbUrl = clip.video_existe
      ? `/api/projects/thumbnail?path=${encodeURIComponent(state.project)}&clip_id=${clip.id}`
      : "";

    card.innerHTML = `
      <div class="clip-thumb">
        ${clip.estrela ? '<span class="star">⭐</span>' : ""}
        <span class="badge ${clip.upload_status}">${clip.upload_status}</span>
        ${thumbUrl ? `<img loading="lazy" src="${thumbUrl}">` : ""}
      </div>
      <div class="clip-body">
        <p class="clip-title">${escapeHtml(clip.titulo)}</p>
        <div class="clip-meta">${clip.start}–${clip.end} · ${clip.duracao}</div>
        <div class="clip-score"><i style="width:${(clip.score / 10) * 100}%"></i></div>
      </div>
    `;
    if (clip.video_existe) {
      card.querySelector(".clip-thumb").addEventListener("click", () => openVideoModal(clip));
      card.style.cursor = "pointer";
    }
    grid.appendChild(card);
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

qs("clips-filter").addEventListener("change", renderClipGrid);
qs("clips-sort").addEventListener("change", renderClipGrid);
qs("clips-refresh").addEventListener("click", loadClips);

function openVideoModal(clip) {
  const url = `/api/projects/video?path=${encodeURIComponent(state.project)}&clip_id=${clip.id}`;
  qs("modal-video").src = url;
  qs("video-modal").classList.add("show");
}
qs("modal-close").addEventListener("click", () => {
  qs("video-modal").classList.remove("show");
  qs("modal-video").pause();
  qs("modal-video").src = "";
});
qs("video-modal").addEventListener("click", (event) => {
  if (event.target.id === "video-modal") qs("modal-close").click();
});

// --- fila / agendamento ------------------------------------------------------

async function loadQueue() {
  if (!state.project) return;
  const summary = await api(`/api/projects/queue-summary?path=${encodeURIComponent(state.project)}`);
  qs("queue-stats").innerHTML = Object.entries(summary.por_status)
    .map(([status, count]) => `<div class="stat"><b>${count}</b><span>${status}</span></div>`)
    .concat(`<div class="stat"><b>${summary.total}</b><span>total</span></div>`)
    .join("");

  const body = qs("queue-table-body");
  const sorted = [...state.clips].sort((a, b) => (a.publish_at || "").localeCompare(b.publish_at || ""));
  body.innerHTML = sorted
    .map(
      (c) => `<tr>
        <td>${escapeHtml(c.titulo.slice(0, 46))}</td>
        <td><span class="pill ${c.upload_status}">${c.upload_status}</span></td>
        <td>${c.publish_at ? new Date(c.publish_at).toLocaleString("pt-BR") : "—"}</td>
        <td style="font-family:ui-monospace,monospace">${c.video_id || "—"}</td>
      </tr>`
    )
    .join("");
}

let scheduleMode = true;
qs("publish-schedule-toggle").addEventListener("click", () => {
  scheduleMode = !scheduleMode;
  qs("publish-schedule-toggle").classList.toggle("on", scheduleMode);
  qs("publish-schedule-label").textContent = scheduleMode
    ? "Agendar nos horários configurados"
    : "Publicar direto (privado)";
});

qs("publish-btn").addEventListener("click", async () => {
  const button = qs("publish-btn");
  const output = qs("publish-result");
  button.disabled = true;
  output.textContent = "Publicando — isso pode levar alguns minutos por vídeo...";
  try {
    const result = await api("/api/publish", {
      method: "POST",
      body: JSON.stringify({
        project_path: state.project,
        max_uploads: parseInt(qs("publish-count").value, 10),
        schedule: scheduleMode,
      }),
    });
    output.textContent = result.stdout.join("\n");
    showToast("Lote processado.");
    loadClips();
    loadQueue();
  } catch (error) {
    output.textContent = "Erro: " + error.message;
    showToast("Falha ao publicar: " + error.message, true);
  } finally {
    button.disabled = false;
  }
});

// --- estilo -----------------------------------------------------------------

async function loadConfig() {
  const { config } = await api("/api/config");
  state.config = config;

  document.querySelectorAll(".style-option").forEach((el) => {
    el.classList.toggle("selected", el.dataset.style === config.subtitle_style);
    el.addEventListener("click", () => {
      document.querySelectorAll(".style-option").forEach((o) => o.classList.remove("selected"));
      el.classList.add("selected");
    });
  });

  qs("subtitle-size").value = config.subtitle_size_ratio;
  qs("subtitle-size-val").textContent = config.subtitle_size_ratio.toFixed(3);

  qs("signature-size").value = config.signature_size_ratio;
  qs("signature-size-val").textContent = config.signature_size_ratio.toFixed(2);
  if (config.signature_path) {
    qs("sig-preview").style.backgroundImage = `url(/api/config/signature/preview?_=${Date.now()})`;
  }

  qs("zoom-toggle").classList.toggle("on", config.zoom_enabled);
  qs("zoom-fields").style.opacity = config.zoom_enabled ? "1" : ".45";
  qs("zoom-amplitude").value = config.zoom_amplitude;
  qs("zoom-amplitude-val").textContent = config.zoom_amplitude.toFixed(2);
  qs("zoom-transition").value = config.zoom_transition_s;
  qs("zoom-transition-val").textContent = config.zoom_transition_s + "s";
  qs("zoom-hold-auto-toggle").classList.toggle("on", config.zoom_hold_auto);
  qs("zoom-hold-manual-row").style.display = config.zoom_hold_auto ? "none" : "flex";
  qs("zoom-hold").value = config.zoom_hold_s;
  qs("zoom-hold-val").textContent = config.zoom_hold_s + "s";

  renderSlots(config.schedule_slots);
}

function bindRangeDisplay(inputId, labelId, formatter) {
  const input = qs(inputId);
  input.addEventListener("input", () => {
    qs(labelId).textContent = formatter(parseFloat(input.value));
  });
}
bindRangeDisplay("subtitle-size", "subtitle-size-val", (v) => v.toFixed(3));
bindRangeDisplay("signature-size", "signature-size-val", (v) => v.toFixed(2));
bindRangeDisplay("zoom-amplitude", "zoom-amplitude-val", (v) => v.toFixed(2));
bindRangeDisplay("zoom-transition", "zoom-transition-val", (v) => v + "s");
bindRangeDisplay("zoom-hold", "zoom-hold-val", (v) => v + "s");

qs("zoom-toggle").addEventListener("click", () => {
  const on = qs("zoom-toggle").classList.toggle("on");
  qs("zoom-fields").style.opacity = on ? "1" : ".45";
});
qs("zoom-hold-auto-toggle").addEventListener("click", () => {
  const on = qs("zoom-hold-auto-toggle").classList.toggle("on");
  qs("zoom-hold-manual-row").style.display = on ? "none" : "flex";
});

qs("sig-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  try {
    const response = await fetch("/api/config/signature", { method: "POST", body: formData });
    if (!response.ok) throw new Error("upload falhou");
    const { signature_path } = await response.json();
    qs("sig-preview").style.backgroundImage = `url(/api/config/signature/preview?_=${Date.now()})`;
    showToast("Assinatura atualizada.");
  } catch (error) {
    showToast("Erro ao subir assinatura: " + error.message, true);
  }
});

qs("style-save").addEventListener("click", async () => {
  const selected = document.querySelector(".style-option.selected");
  try {
    await api("/api/config", {
      method: "PUT",
      body: JSON.stringify({
        subtitle_style: selected.dataset.style,
        subtitle_size_ratio: parseFloat(qs("subtitle-size").value),
        signature_size_ratio: parseFloat(qs("signature-size").value),
        zoom_enabled: qs("zoom-toggle").classList.contains("on"),
        zoom_amplitude: parseFloat(qs("zoom-amplitude").value),
        zoom_transition_s: parseFloat(qs("zoom-transition").value),
        zoom_hold_auto: qs("zoom-hold-auto-toggle").classList.contains("on"),
        zoom_hold_s: parseFloat(qs("zoom-hold").value),
      }),
    });
    showToast("Estilo salvo.");
  } catch (error) {
    showToast("Erro ao salvar: " + error.message, true);
  }
});

// --- horários -----------------------------------------------------------

function renderSlots(slots) {
  const container = qs("slot-list");
  container.innerHTML = "";
  for (const slot of slots) addSlotInput(slot);
}

function addSlotInput(value = "08:00") {
  const wrapper = document.createElement("div");
  wrapper.style.display = "flex";
  wrapper.style.gap = "4px";
  const input = document.createElement("input");
  input.type = "text";
  input.value = value;
  input.placeholder = "HH:MM";
  const remove = document.createElement("button");
  remove.className = "btn";
  remove.textContent = "✕";
  remove.addEventListener("click", () => wrapper.remove());
  wrapper.append(input, remove);
  qs("slot-list").appendChild(wrapper);
}

qs("slot-add").addEventListener("click", () => addSlotInput());

qs("settings-save").addEventListener("click", async () => {
  const slots = Array.from(qs("slot-list").querySelectorAll("input")).map((i) => i.value.trim());
  try {
    await api("/api/config", { method: "PUT", body: JSON.stringify({ schedule_slots: slots }) });
    showToast("Horários salvos.");
  } catch (error) {
    showToast("Erro ao salvar horários: " + error.message, true);
  }
});

// --- novo corte ---------------------------------------------------------

async function loadSourceVideos() {
  const videos = await api("/api/source-videos");
  const select = qs("source-video-select");
  select.innerHTML = "";
  if (!videos.length) {
    select.innerHTML = "<option value=''>Nenhum vídeo grande encontrado</option>";
    return;
  }
  for (const v of videos) {
    const option = document.createElement("option");
    option.value = v.path;
    option.textContent = `${v.name} (${v.size_mb} MB)`;
    select.appendChild(option);
  }
  updateSuggestedOutput();
}

async function updateSuggestedOutput() {
  const videoPath = qs("source-video-select").value;
  if (!videoPath) return;
  const { output_dir } = await api(`/api/source-videos/suggest-output?video_path=${encodeURIComponent(videoPath)}`);
  qs("output-dir").value = output_dir;
}

qs("source-video-select").addEventListener("change", updateSuggestedOutput);
qs("source-video-refresh").addEventListener("click", loadSourceVideos);

let currentJobId = null;
let jobPollTimer = null;

qs("dry-run-btn").addEventListener("click", async () => {
  const videoPath = qs("source-video-select").value;
  if (!videoPath) { showToast("Escolha um vídeo primeiro.", true); return; }

  try {
    const { job_id } = await api("/api/process/dry-run", {
      method: "POST",
      body: JSON.stringify({
        video_path: videoPath,
        output_dir: qs("output-dir").value,
        language: qs("new-language").value,
        min_clip_duration: parseFloat(qs("new-min-duration").value),
        max_clip_duration: parseFloat(qs("new-max-duration").value),
        word_timestamps: true,
      }),
    });
    currentJobId = job_id;
    qs("job-panel").style.display = "block";
    qs("job-title").textContent = "Transcrevendo e propondo cortes...";
    qs("job-actions").innerHTML = "";
    pollJob();
  } catch (error) {
    showToast("Erro ao iniciar: " + error.message, true);
  }
});

function pollJob() {
  clearTimeout(jobPollTimer);
  jobPollTimer = setTimeout(async () => {
    try {
      const status = await api(`/api/process/status?job_id=${currentJobId}`);
      qs("job-log").textContent = status.log_tail.join("\n");
      qs("job-log").scrollTop = qs("job-log").scrollHeight;

      if (status.running) {
        pollJob();
        return;
      }

      if (status.returncode === 0 && status.kind === "dry_run") {
        qs("job-title").textContent = "Cortes propostos — revise na aba Clips antes de renderizar.";
        qs("job-actions").innerHTML = "";
        const renderButton = document.createElement("button");
        renderButton.className = "btn primary";
        renderButton.textContent = "Renderizar com o estilo atual";
        renderButton.addEventListener("click", () => startRender(status.output_dir, qs("source-video-select").value));
        qs("job-actions").appendChild(renderButton);
        loadProjects();
      } else if (status.returncode === 0 && status.kind === "render") {
        qs("job-title").textContent = "Render concluído.";
        loadProjects();
        loadClips();
      } else {
        qs("job-title").textContent = `Terminou com erro (código ${status.returncode}).`;
      }
    } catch (error) {
      qs("job-title").textContent = "Erro ao acompanhar o job: " + error.message;
    }
  }, 2000);
}

async function startRender(outputDir, videoPath) {
  try {
    const { job_id } = await api("/api/process/render", {
      method: "POST",
      body: JSON.stringify({
        video_path: videoPath,
        output_dir: outputDir,
        language: qs("new-language").value,
        min_clip_duration: parseFloat(qs("new-min-duration").value),
        max_clip_duration: parseFloat(qs("new-max-duration").value),
      }),
    });
    currentJobId = job_id;
    qs("job-title").textContent = "Renderizando (legenda, zoom, assinatura)...";
    qs("job-actions").innerHTML = "";
    pollJob();
  } catch (error) {
    showToast("Erro ao renderizar: " + error.message, true);
  }
}

// --- conectores -----------------------------------------------------------

async function checkYoutubeConnector() {
  qs("youtube-status").textContent = "Verificando…";
  try {
    const status = await api("/api/connectors/youtube");
    if (status.connected) {
      qs("youtube-status").innerHTML = `
        <div class="field-row"><label>Status</label><span class="pill publicado">Conectado</span></div>
        <div class="field-row"><label>Canal</label><span>${escapeHtml(status.channel_title)}</span></div>
        <div class="field-row"><label>Inscritos</label><span>${status.subscriber_count}</span></div>
        <div class="field-row"><label>Vídeos públicos</label><span>${status.video_count}</span></div>
      `;
    } else {
      qs("youtube-status").innerHTML = `<span class="pill" style="background:var(--warn-soft);color:var(--warn)">Não conectado</span> — ${escapeHtml(String(status.reason))}`;
    }
  } catch (error) {
    qs("youtube-status").textContent = "Erro ao verificar: " + error.message;
  }
}

async function checkMcpConnector() {
  qs("mcp-status").textContent = "Verificando…";
  try {
    const status = await api("/api/connectors/mcp");
    const pillClass = status.registered ? "publicado" : "pendente";
    const label = status.registered ? "Registrado" : "Não registrado";
    qs("mcp-status").innerHTML = `
      <div class="field-row"><label>Status</label><span class="pill ${pillClass}">${label}</span></div>
      <pre style="font-size:11px; background:var(--surface-sunk); padding:10px; border-radius:8px; white-space:pre-wrap">${escapeHtml(status.detail)}</pre>
    `;
  } catch (error) {
    qs("mcp-status").textContent = "Erro ao verificar: " + error.message;
  }
}

qs("youtube-check").addEventListener("click", checkYoutubeConnector);
qs("mcp-check").addEventListener("click", checkMcpConnector);

// --- boot -----------------------------------------------------------------

(async function init() {
  try {
    await loadProjects();
    await Promise.all([
      loadClips(),
      loadQueue(),
      loadConfig(),
      loadSourceVideos(),
      checkYoutubeConnector(),
      checkMcpConnector(),
    ]);
  } catch (error) {
    showToast("Erro ao carregar painel: " + error.message, true);
  }
})();
