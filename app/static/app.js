/* Clipper Flow — painel local.
   O vocabulário visual vem da biblioteca do design system; aqui só o estado. */

const state = { project: null, clips: [], config: null, jobId: null };

const STATUS_BADGE = { publicado: "ok", agendado: "alerta", pendente: "neutro", nao_enfileirado: "neutro" };
/* O status vem do backend em snake_case; a tela mostra a palavra que a pessoa usa. */
const STATUS_ROTULO = {
  publicado: "publicado",
  agendado: "agendado",
  pendente: "na fila",
  nao_enfileirado: "sem fila",
};
const rotuloStatus = (s) => STATUS_ROTULO[s] ?? s;
const TITULOS_VISTA = {
  new: "Novo corte",
  clips: "Clipes",
  queue: "Publicação",
  style: "Estilo",
  settings: "Horários",
  connectors: "Conectores",
  docs: "Documentação",
};

const qs = (id) => document.getElementById(id);

// --- utilidades ---------------------------------------------------------

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

/** Toast da biblioteca: ícone + texto, sai sozinho. */
function toast(mensagem, tipo = "ok") {
  const el = document.createElement("div");
  el.className = `toast toast--${tipo}`;
  el.setAttribute("role", tipo === "erro" ? "alert" : "status");
  el.innerHTML = `<svg aria-hidden="true"><use href="#${tipo === "erro" ? "i-alerta" : "i-check"}"/></svg><span></span>`;
  el.querySelector("span").textContent = mensagem;
  qs("toasts").appendChild(el);
  setTimeout(() => el.remove(), tipo === "erro" ? 5200 : 2800);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(Array.isArray(detail?.issues) ? detail.issues.join("; ") : (detail || "erro"));
  }
  return body;
}

function kpiCard(valor, rotulo) {
  return `<div class="card"><div class="kpi">
    <span class="kpi__rotulo">${escapeHtml(rotulo)}</span>
    <span class="kpi__valor">${escapeHtml(String(valor))}</span>
  </div></div>`;
}

// --- navegação e chrome -------------------------------------------------

/** Troca de vista pelo hash: dá link direto para cada tela e faz o botão
    "voltar" do navegador funcionar, em vez de sair do app. */
function irPara(vista) {
  if (!TITULOS_VISTA[vista]) vista = "clips";
  document.querySelectorAll(".nav-item[data-view]").forEach((b) => {
    b.toggleAttribute("aria-current", b.dataset.view === vista);
    if (b.dataset.view === vista) b.setAttribute("aria-current", "page");
  });
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("is-ativa", v.id === `view-${vista}`));
  qs("topo-titulo").textContent = TITULOS_VISTA[vista];
}

document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
  btn.addEventListener("click", () => { location.hash = btn.dataset.view; });
});
window.addEventListener("hashchange", () => irPara(location.hash.slice(1)));

qs("app").querySelector("[data-colapso]").addEventListener("click", (event) => {
  const colapsada = qs("app").classList.toggle("app--colapsada");
  event.currentTarget.setAttribute("aria-expanded", String(!colapsada));
});

document.querySelectorAll(".seg [data-tema]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.documentElement.dataset.tema = btn.dataset.tema;
    document.querySelectorAll(".seg [data-tema]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === btn))
    );
  });
});

// --- projetos -----------------------------------------------------------

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
    option.textContent = `${project.name} · ${project.clip_count}`;
    select.appendChild(option);
  }
  select.value = state.project ?? projects[0].path;
  state.project = select.value;
}

qs("project-select").addEventListener("change", (event) => {
  state.project = event.target.value;
  loadClips().then(loadQueue);
});

// --- clipes -------------------------------------------------------------

async function loadClips() {
  if (!state.project) return;
  state.clips = await api(`/api/projects/clips?path=${encodeURIComponent(state.project)}`);
  renderClipKpis();
  renderClipGrid();
  qs("nav-contagem-clipes").textContent = state.clips.length || "";
}

function renderClipKpis() {
  const porStatus = {};
  for (const c of state.clips) porStatus[c.upload_status] = (porStatus[c.upload_status] || 0) + 1;
  const minutos = state.clips.reduce((soma, c) => soma + (parseFloat(c.duracao) || 0), 0) / 60;

  qs("clips-kpis").innerHTML = [
    kpiCard(state.clips.length, "clipes"),
    kpiCard(porStatus.publicado || 0, "publicados"),
    kpiCard(porStatus.agendado || 0, "agendados"),
    kpiCard(porStatus.pendente || 0, "pendentes"),
    kpiCard(`${minutos.toFixed(0)} min`, "material"),
  ].join("");
}

function renderClipGrid() {
  const filtro = qs("clips-filter").value;
  const ordem = qs("clips-sort").value;

  let lista = state.clips.filter((c) => filtro === "todos" || c.upload_status === filtro);
  if (ordem === "score") lista = [...lista].sort((a, b) => b.score - a.score);

  const grade = qs("clip-grid");
  grade.innerHTML = "";

  if (!lista.length) {
    grade.innerHTML = `<div class="vazio" style="grid-column:1/-1">
      <p class="vazio__titulo">Nenhum clipe com esse filtro</p>
      <p class="vazio__texto">Troque o status no seletor acima, ou gere novos cortes na aba Novo corte.</p>
    </div>`;
    return;
  }

  for (const clip of lista) {
    const card = document.createElement(clip.video_existe ? "button" : "div");
    card.className = `clipe${clip.video_existe ? " clipe--abre" : ""}`;
    if (clip.video_existe) card.type = "button";

    const thumb = clip.video_existe
      ? `<img loading="lazy" alt="" src="/api/projects/thumbnail?path=${encodeURIComponent(state.project)}&clip_id=${clip.id}">`
      : "";

    card.innerHTML = `
      <span class="clipe__palco">
        ${clip.estrela ? '<span class="clipe__estrela" title="Aposta de alcance">⭐</span>' : ""}
        ${thumb}
        <span class="clipe__play" aria-hidden="true"><svg><use href="#i-play"/></svg></span>
        <span class="clipe__selo">
          <span class="badge badge--${STATUS_BADGE[clip.upload_status] ?? "neutro"}">${escapeHtml(rotuloStatus(clip.upload_status))}</span>
        </span>
      </span>
      <span class="clipe__corpo">
        <span class="clipe__titulo">${escapeHtml(clip.titulo)}</span>
        <span class="clipe__meta">${escapeHtml(clip.start)}–${escapeHtml(clip.end)} · ${escapeHtml(clip.duracao)}</span>
        <span class="progresso clipe__score"><i style="--p:${(clip.score / 10) * 100}%"></i></span>
      </span>`;

    if (clip.video_existe) card.addEventListener("click", () => abrirVideo(clip));
    grade.appendChild(card);
  }
}

qs("clips-filter").addEventListener("change", renderClipGrid);
qs("clips-sort").addEventListener("change", renderClipGrid);
qs("clips-refresh").addEventListener("click", () => loadClips().then(() => toast("Lista atualizada.")));

// --- modal de vídeo -----------------------------------------------------

let ultimoFoco = null;

function abrirVideo(clip) {
  ultimoFoco = document.activeElement;
  qs("modal-titulo").textContent = clip.titulo;
  qs("modal-video").src = `/api/projects/video?path=${encodeURIComponent(state.project)}&clip_id=${clip.id}`;
  qs("video-modal").hidden = false;
  qs("modal-close").focus();
}

function fecharVideo() {
  qs("video-modal").hidden = true;
  qs("modal-video").pause();
  qs("modal-video").src = "";
  ultimoFoco?.focus();
}

qs("modal-close").addEventListener("click", fecharVideo);
qs("video-modal").addEventListener("click", (event) => {
  if (event.target.id === "video-modal") fecharVideo();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !qs("video-modal").hidden) fecharVideo();
});

// --- fila de publicação -------------------------------------------------

async function loadQueue() {
  if (!state.project) return;
  const resumo = await api(`/api/projects/queue-summary?path=${encodeURIComponent(state.project)}`);

  qs("queue-kpis").innerHTML = Object.entries(resumo.por_status)
    .map(([status, n]) => kpiCard(n, rotuloStatus(status)))
    .concat(kpiCard(resumo.total, "total"))
    .join("");

  const pendentes = resumo.por_status.pendente || 0;
  qs("nav-contagem-fila").textContent = pendentes || "";

  const ordenados = [...state.clips].sort((a, b) =>
    (a.publish_at || "￿").localeCompare(b.publish_at || "￿")
  );
  qs("queue-table-body").innerHTML = ordenados
    .map((c) => `<tr>
      <td class="prim">${escapeHtml(c.titulo.slice(0, 44))}</td>
      <td><span class="badge badge--${STATUS_BADGE[c.upload_status] ?? "neutro"}">${escapeHtml(rotuloStatus(c.upload_status))}</span></td>
      <td>${c.publish_at ? escapeHtml(new Date(c.publish_at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })) : "—"}</td>
      <td class="mono">${escapeHtml(c.video_id || "—")}</td>
    </tr>`)
    .join("");
}

qs("publish-schedule").addEventListener("change", (event) => {
  qs("publish-schedule-label").textContent = event.target.checked
    ? "Nos horários configurados"
    : "Publicar direto, sem data";
});

qs("publish-btn").addEventListener("click", async (event) => {
  const botao = event.currentTarget;
  const saida = qs("publish-result");
  botao.dataset.carregando = "1";
  saida.hidden = false;
  saida.textContent = "Enviando — cada vídeo leva alguns minutos…";

  try {
    const resultado = await api("/api/publish", {
      method: "POST",
      body: JSON.stringify({
        project_path: state.project,
        max_uploads: parseInt(qs("publish-count").value, 10),
        schedule: qs("publish-schedule").checked,
      }),
    });
    saida.textContent = resultado.stdout.join("\n");
    toast("Lote processado.");
    await loadClips();
    await loadQueue();
  } catch (error) {
    saida.textContent = "Erro: " + error.message;
    toast("Falha ao publicar: " + error.message, "erro");
  } finally {
    delete botao.dataset.carregando;
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
    option.textContent = `${v.name} · ${(v.size_mb / 1024).toFixed(1)} GB`;
    select.appendChild(option);
  }
  await sugerirSaida();
}

async function sugerirSaida() {
  const videoPath = qs("source-video-select").value;
  if (!videoPath) return;
  const { output_dir } = await api(`/api/source-videos/suggest-output?video_path=${encodeURIComponent(videoPath)}`);
  qs("output-dir").value = output_dir;
}

qs("source-video-select").addEventListener("change", sugerirSaida);
qs("source-video-refresh").addEventListener("click", () => loadSourceVideos().then(() => toast("Lista atualizada.")));

function opcoesProcessamento() {
  return {
    video_path: qs("source-video-select").value,
    output_dir: qs("output-dir").value,
    language: qs("new-language").value,
    min_clip_duration: parseFloat(qs("new-min-duration").value),
    max_clip_duration: parseFloat(qs("new-max-duration").value),
  };
}

qs("dry-run-btn").addEventListener("click", async (event) => {
  if (!qs("source-video-select").value) {
    toast("Escolha um vídeo primeiro.", "erro");
    return;
  }
  const botao = event.currentTarget;
  botao.dataset.carregando = "1";
  try {
    const { job_id } = await api("/api/process/dry-run", {
      method: "POST",
      body: JSON.stringify({ ...opcoesProcessamento(), word_timestamps: true }),
    });
    state.jobId = job_id;
    qs("job-panel").hidden = false;
    qs("job-title").textContent = "Transcrevendo e propondo cortes…";
    qs("job-badge").className = "badge badge--info";
    qs("job-badge").textContent = "em curso";
    qs("job-actions").innerHTML = "";
    acompanharJob();
  } catch (error) {
    toast("Erro ao iniciar: " + error.message, "erro");
  } finally {
    delete botao.dataset.carregando;
  }
});

let timerJob = null;

function acompanharJob() {
  clearTimeout(timerJob);
  timerJob = setTimeout(async () => {
    try {
      const status = await api(`/api/process/status?job_id=${state.jobId}`);
      const log = qs("job-log");
      log.textContent = status.log_tail.join("\n");
      log.scrollTop = log.scrollHeight;

      if (status.running) {
        acompanharJob();
        return;
      }

      const ok = status.returncode === 0;
      qs("job-badge").className = `badge badge--${ok ? "ok" : "erro"}`;
      qs("job-badge").textContent = ok ? "concluído" : "falhou";

      if (ok && status.kind === "dry_run") {
        qs("job-title").textContent = "Cortes propostos — revise em Clipes antes de renderizar";
        const botao = document.createElement("button");
        botao.className = "acao acao--primaria";
        botao.type = "button";
        botao.textContent = "Renderizar com o estilo atual";
        botao.addEventListener("click", () => renderizar(status.output_dir));
        qs("job-actions").replaceChildren(botao);
        await loadProjects();
      } else if (ok) {
        qs("job-title").textContent = "Render concluído";
        qs("job-actions").innerHTML = "";
        await loadProjects();
        await loadClips();
      } else {
        qs("job-title").textContent = `Terminou com erro (código ${status.returncode})`;
      }
    } catch (error) {
      qs("job-title").textContent = "Erro ao acompanhar: " + error.message;
      qs("job-badge").className = "badge badge--erro";
      qs("job-badge").textContent = "erro";
    }
  }, 2000);
}

async function renderizar(outputDir) {
  try {
    const { job_id } = await api("/api/process/render", {
      method: "POST",
      body: JSON.stringify({ ...opcoesProcessamento(), output_dir: outputDir }),
    });
    state.jobId = job_id;
    qs("job-title").textContent = "Renderizando legenda, zoom e assinatura…";
    qs("job-badge").className = "badge badge--info";
    qs("job-badge").textContent = "em curso";
    qs("job-actions").innerHTML = "";
    acompanharJob();
  } catch (error) {
    toast("Erro ao renderizar: " + error.message, "erro");
  }
}

// --- estilo -------------------------------------------------------------

function ligarDeslizante(idInput, idValor, formatar) {
  const input = qs(idInput);
  const mostrar = () => { qs(idValor).textContent = formatar(parseFloat(input.value)); };
  input.addEventListener("input", mostrar);
  return mostrar;
}

const mostrarTamanhoLegenda = ligarDeslizante("subtitle-size", "subtitle-size-val", (v) => v.toFixed(3));
const mostrarTamanhoAssinatura = ligarDeslizante("signature-size", "signature-size-val", (v) => v.toFixed(2));
const mostrarAmplitude = ligarDeslizante("zoom-amplitude", "zoom-amplitude-val", (v) => `${(v * 100).toFixed(0)}%`);
const mostrarTransicao = ligarDeslizante("zoom-transition", "zoom-transition-val", (v) => `${v}s`);
const mostrarPausa = ligarDeslizante("zoom-hold", "zoom-hold-val", (v) => `${v}s`);

function aplicarEstadoZoom() {
  const ligado = qs("zoom-enabled").checked;
  qs("zoom-fields").style.opacity = ligado ? "1" : ".45";
  qs("zoom-fields").querySelectorAll("input").forEach((i) => { i.disabled = !ligado; });
  qs("zoom-hold-manual-row").hidden = qs("zoom-hold-auto").checked;
}

qs("zoom-enabled").addEventListener("change", aplicarEstadoZoom);
qs("zoom-hold-auto").addEventListener("change", aplicarEstadoZoom);

async function loadConfig() {
  const { config } = await api("/api/config");
  state.config = config;

  const radio = document.querySelector(`input[name="subtitle-style"][value="${config.subtitle_style}"]`);
  if (radio) radio.checked = true;

  qs("subtitle-size").value = config.subtitle_size_ratio;
  qs("signature-size").value = config.signature_size_ratio;
  qs("zoom-enabled").checked = config.zoom_enabled;
  qs("zoom-amplitude").value = config.zoom_amplitude;
  qs("zoom-transition").value = config.zoom_transition_s;
  qs("zoom-hold-auto").checked = config.zoom_hold_auto;
  qs("zoom-hold").value = config.zoom_hold_s;

  mostrarTamanhoLegenda();
  mostrarTamanhoAssinatura();
  mostrarAmplitude();
  mostrarTransicao();
  mostrarPausa();
  aplicarEstadoZoom();
  mostrarAssinatura(config.signature_path);
  renderSlots(config.schedule_slots);
}

function mostrarAssinatura(caminho) {
  const previa = qs("sig-preview");
  if (caminho) {
    previa.style.backgroundImage = `url(/api/config/signature/preview?_=${Date.now()})`;
    previa.classList.remove("assinatura__vazia");
    previa.textContent = "";
  } else {
    previa.style.backgroundImage = "";
    previa.classList.add("assinatura__vazia");
    previa.textContent = "sem imagem";
  }
}

qs("sig-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  try {
    const response = await fetch("/api/config/signature", { method: "POST", body: formData });
    if (!response.ok) throw new Error("upload recusado");
    const { signature_path } = await response.json();
    mostrarAssinatura(signature_path);
    toast("Assinatura atualizada.");
  } catch (error) {
    toast("Erro ao enviar: " + error.message, "erro");
  }
});

qs("style-save").addEventListener("click", async (event) => {
  const botao = event.currentTarget;
  botao.dataset.carregando = "1";
  try {
    await api("/api/config", {
      method: "PUT",
      body: JSON.stringify({
        subtitle_style: document.querySelector('input[name="subtitle-style"]:checked').value,
        subtitle_size_ratio: parseFloat(qs("subtitle-size").value),
        signature_size_ratio: parseFloat(qs("signature-size").value),
        zoom_enabled: qs("zoom-enabled").checked,
        zoom_amplitude: parseFloat(qs("zoom-amplitude").value),
        zoom_transition_s: parseFloat(qs("zoom-transition").value),
        zoom_hold_auto: qs("zoom-hold-auto").checked,
        zoom_hold_s: parseFloat(qs("zoom-hold").value),
      }),
    });
    toast("Estilo salvo.");
  } catch (error) {
    toast("Erro ao salvar: " + error.message, "erro");
  } finally {
    delete botao.dataset.carregando;
  }
});

// --- horários -----------------------------------------------------------

function renderSlots(slots) {
  qs("slot-list").innerHTML = "";
  for (const slot of slots) adicionarSlot(slot);
}

function adicionarSlot(valor = "08:00") {
  const wrapper = document.createElement("div");
  wrapper.className = "horario";

  const input = document.createElement("input");
  input.className = "input";
  input.type = "text";
  input.value = valor;
  input.placeholder = "HH:MM";
  input.setAttribute("aria-label", "Horário de publicação");

  const remover = document.createElement("button");
  remover.className = "acao acao--sutil acao--icone acao--pequena";
  remover.type = "button";
  remover.setAttribute("aria-label", `Remover horário ${valor}`);
  remover.innerHTML = '<svg><use href="#i-x"/></svg>';
  remover.addEventListener("click", () => wrapper.remove());

  wrapper.append(input, remover);
  qs("slot-list").appendChild(wrapper);
}

qs("slot-add").addEventListener("click", () => adicionarSlot());

qs("settings-save").addEventListener("click", async (event) => {
  const botao = event.currentTarget;
  botao.dataset.carregando = "1";
  const slots = [...qs("slot-list").querySelectorAll("input")].map((i) => i.value.trim());
  try {
    await api("/api/config", { method: "PUT", body: JSON.stringify({ schedule_slots: slots }) });
    toast("Horários salvos.");
  } catch (error) {
    toast("Erro ao salvar: " + error.message, "erro");
  } finally {
    delete botao.dataset.carregando;
  }
});

// --- conectores ---------------------------------------------------------

function linhaConector(chave, valor, mono = false) {
  return `<div class="conector__linha">
    <span class="conector__chave">${escapeHtml(chave)}</span>
    <span class="conector__valor${mono ? " conector__valor--mono" : ""}">${valor}</span>
  </div>`;
}

async function checarYoutube() {
  const alvo = qs("youtube-status");
  alvo.innerHTML = '<span class="skel" style="height:14px;width:60%"></span>';
  try {
    const status = await api("/api/connectors/youtube");
    if (status.connected) {
      alvo.innerHTML =
        linhaConector("Status", '<span class="badge badge--ok">conectado</span>') +
        linhaConector("Canal", escapeHtml(status.channel_title)) +
        linhaConector("Inscritos", escapeHtml(status.subscriber_count)) +
        linhaConector("Vídeos públicos", escapeHtml(status.video_count)) +
        linhaConector("Credencial", escapeHtml(status.credential_path), true);
    } else {
      alvo.innerHTML =
        linhaConector("Status", '<span class="badge badge--alerta">desconectado</span>') +
        linhaConector("Motivo", escapeHtml(String(status.reason)));
    }
  } catch (error) {
    alvo.innerHTML = `<div class="erro-bloco"><svg><use href="#i-alerta"/></svg><div><b>Não deu para verificar</b><p>${escapeHtml(error.message)}</p></div></div>`;
  }
}

async function checarMcp() {
  const alvo = qs("mcp-status");
  alvo.innerHTML = '<span class="skel" style="height:14px;width:60%"></span>';
  try {
    const status = await api("/api/connectors/mcp");
    const badge = status.registered
      ? '<span class="badge badge--ok">registrado</span>'
      : '<span class="badge badge--neutro">não registrado</span>';
    alvo.innerHTML =
      linhaConector("Status", badge) +
      `<pre class="log" style="max-height:150px">${escapeHtml(status.detail)}</pre>`;
  } catch (error) {
    alvo.innerHTML = `<div class="erro-bloco"><svg><use href="#i-alerta"/></svg><div><b>Não deu para verificar</b><p>${escapeHtml(error.message)}</p></div></div>`;
  }
}

qs("youtube-check").addEventListener("click", checarYoutube);
qs("mcp-check").addEventListener("click", checarMcp);

// --- início -------------------------------------------------------------

(async function iniciar() {
  irPara(location.hash.slice(1) || "clips");
  try {
    await loadProjects();
    await Promise.all([
      loadClips().then(loadQueue),
      loadConfig(),
      loadSourceVideos(),
      checarYoutube(),
      checarMcp(),
    ]);
  } catch (error) {
    toast("Erro ao carregar: " + error.message, "erro");
  }
})();
