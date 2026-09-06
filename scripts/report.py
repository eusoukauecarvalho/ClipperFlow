#!/usr/bin/env python3
"""Gera uma página HTML de revisão dos cortes a partir do metadata.json.

Revisar 60 clips no JSON cru é inviável. A página lista os cortes com transcrição,
timecode e score, permite ordenar/filtrar e marcar os aprovados (persistidos no
navegador de quem revisa).

Uso:
    python3 report.py --metadata output/metadata.json --output output/revisao.html
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TEMPLATE = """<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,500&display=swap">
<style>
  :root {{
    --ground: #F4F6F4;
    --surface: #FFFFFF;
    --surface-sunk: #EDF0EE;
    --ink: #111917;
    --ink-soft: #5C6C68;
    --line: #DCE2DF;
    --accent: #1C6C67;
    --accent-soft: #DCECEA;
    --shadow: 0 1px 2px rgba(17, 25, 23, .06), 0 8px 24px -16px rgba(17, 25, 23, .28);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --ground: #0C1211;
      --surface: #151D1B;
      --surface-sunk: #101817;
      --ink: #E7EDEA;
      --ink-soft: #8B9C97;
      --line: #24302D;
      --accent: #58B8B0;
      --accent-soft: #17302E;
      --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px -16px rgba(0, 0, 0, .8);
    }}
  }}
  :root[data-theme="dark"] {{
    --ground: #0C1211;
    --surface: #151D1B;
    --surface-sunk: #101817;
    --ink: #E7EDEA;
    --ink-soft: #8B9C97;
    --line: #24302D;
    --accent: #58B8B0;
    --accent-soft: #17302E;
    --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px -16px rgba(0, 0, 0, .8);
  }}

  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--ground);
    color: var(--ink);
    font-family: Archivo, system-ui, -apple-system, sans-serif;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 74ch; margin: 0 auto; padding: 0 20px 96px; }}

  header {{ padding: 56px 0 28px; }}
  .eyebrow {{
    font-size: 11px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase;
    color: var(--accent); margin: 0 0 10px;
  }}
  h1 {{ font-size: clamp(30px, 5vw, 42px); font-weight: 700; letter-spacing: -.02em; margin: 0 0 8px; text-wrap: balance; }}
  .sub {{ color: var(--ink-soft); margin: 0; font-size: 15px; }}

  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 1px;
            background: var(--line); border: 1px solid var(--line); border-radius: 10px;
            overflow: hidden; margin: 28px 0 0; }}
  .stat {{ background: var(--surface); padding: 14px 16px; }}
  .stat b {{ display: block; font-family: "IBM Plex Mono", ui-monospace, monospace;
             font-size: 21px; font-weight: 500; font-variant-numeric: tabular-nums; letter-spacing: -.02em; }}
  .stat span {{ font-size: 11px; color: var(--ink-soft); text-transform: uppercase; letter-spacing: .08em; }}

  .controls {{ position: sticky; top: 0; z-index: 5; display: flex; flex-wrap: wrap; gap: 8px;
               align-items: center; padding: 14px 0; margin: 24px 0 8px;
               background: var(--ground); border-bottom: 1px solid var(--line); }}
  button {{ font: inherit; font-size: 13px; font-weight: 500; color: var(--ink);
            background: var(--surface); border: 1px solid var(--line); border-radius: 999px;
            padding: 7px 14px; cursor: pointer; }}
  button:hover {{ border-color: var(--accent); }}
  button[aria-pressed="true"] {{ background: var(--accent); border-color: var(--accent); color: var(--ground); }}
  button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
  .count {{ margin-left: auto; font-size: 13px; color: var(--ink-soft);
            font-variant-numeric: tabular-nums; }}

  .clip {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
           padding: 18px 20px; margin: 0 0 12px; box-shadow: var(--shadow); }}
  .clip[data-picked="1"] {{ border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent), var(--shadow); }}
  .clip-head {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px; margin-bottom: 12px; }}
  .cid {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 13px; font-weight: 500; color: var(--accent); }}
  .tc {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 13px;
         color: var(--ink-soft); font-variant-numeric: tabular-nums; }}
  .pill {{ font-size: 11px; letter-spacing: .04em; text-transform: uppercase; color: var(--ink-soft);
           background: var(--surface-sunk); border-radius: 999px; padding: 3px 9px; }}
  .pill.cut {{ color: var(--accent); background: var(--accent-soft); }}

  .clip-title {{ font-size: 16.5px; font-weight: 600; letter-spacing: -.01em; margin: 0 0 8px; }}
  .clip-desc {{ font-size: 13px; color: var(--ink-soft); margin: -8px 0 14px; }}
  .quote {{ font-family: Newsreader, Georgia, serif; font-size: 17.5px; font-weight: 300;
            line-height: 1.62; margin: 0 0 16px; color: var(--ink); }}

  .clip-foot {{ display: flex; align-items: center; gap: 12px; }}
  .meter {{ flex: 1; height: 3px; background: var(--surface-sunk); border-radius: 2px; overflow: hidden; }}
  .meter i {{ display: block; height: 100%; background: var(--accent); }}
  .score {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 12px;
            color: var(--ink-soft); font-variant-numeric: tabular-nums; }}

  .empty {{ text-align: center; color: var(--ink-soft); padding: 48px 0; display: none; }}
  footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--line);
            font-size: 13px; color: var(--ink-soft); }}
  code {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .92em;
          background: var(--surface-sunk); padding: 1px 5px; border-radius: 4px; }}
  @media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; }} }}
</style>

<div class="wrap">
  <header>
    <p class="eyebrow">{eyebrow}</p>
    <h1>{heading}</h1>
    <p class="sub">{subtitle}</p>
    <div class="stats">{stats}</div>
  </header>

  <div class="controls">
    <button id="sort-time" aria-pressed="true">Cronológico</button>
    <button id="sort-score" aria-pressed="false">Por score</button>
    <button id="only-picked" aria-pressed="false">Só marcados</button>
    <button id="copy">Copiar marcados</button>
    <span class="count" id="count"></span>
  </div>

  <div id="list"></div>
  <p class="empty" id="empty">Nenhum corte com esse filtro.</p>

  <footer>
    Arquivos em <code>{clips_dir}</code>. Cada corte tem um <code>.srt</code> ao lado —
    importe o par no CapCut ou Premiere para legendar.
  </footer>
</div>

<script>
  const CLIPS = {clips_json};
  const KEY = "podcast-clipper:{storage_key}";

  const load = () => {{
    try {{ return new Set(JSON.parse(localStorage.getItem(KEY) || "[]")); }}
    catch (e) {{ return new Set(); }}
  }};
  const save = (set) => {{
    try {{ localStorage.setItem(KEY, JSON.stringify([...set])); }} catch (e) {{}}
  }};

  let picked = load();
  let sort = "time";
  let onlyPicked = false;

  const list = document.getElementById("list");
  const empty = document.getElementById("empty");
  const count = document.getElementById("count");

  function render() {{
    const rows = CLIPS
      .filter(c => !onlyPicked || picked.has(c.id))
      .sort((a, b) => sort === "score" ? b.score - a.score : a.seconds - b.seconds);

    list.replaceChildren(...rows.map(build));
    empty.style.display = rows.length ? "none" : "block";
    count.textContent = `${{picked.size}} de ${{CLIPS.length}} marcados`;
  }}

  function build(c) {{
    const el = document.createElement("article");
    el.className = "clip";
    el.dataset.picked = picked.has(c.id) ? "1" : "0";

    const head = document.createElement("div");
    head.className = "clip-head";
    head.append(
      span("cid", c.id),
      span("tc", `${{c.start}} – ${{c.end}}`),
      span("pill", c.duration),
    );
    if (c.removed && c.removed !== "0.0s") head.append(span("pill cut", `−${{c.removed}} de pausa`));

    if (c.titulo) {{
      const h = document.createElement("h2");
      h.className = "clip-title";
      h.textContent = (c.estrela ? "\u2b50 " : "") + c.titulo;
      el.append(h);
    }}

    const quote = document.createElement("p");
    quote.className = "quote";
    quote.textContent = c.text;

    const foot = document.createElement("div");
    foot.className = "clip-foot";
    const btn = document.createElement("button");
    btn.textContent = picked.has(c.id) ? "Marcado" : "Marcar";
    btn.setAttribute("aria-pressed", picked.has(c.id) ? "true" : "false");
    btn.onclick = () => {{
      picked.has(c.id) ? picked.delete(c.id) : picked.add(c.id);
      save(picked);
      render();
    }};
    const meter = document.createElement("div");
    meter.className = "meter";
    const fill = document.createElement("i");
    fill.style.width = `${{Math.round((c.score / 10) * 100)}}%`;
    meter.append(fill);
    foot.append(btn, meter, span("score", c.score.toFixed(1)));

    el.append(head);
    if (el.querySelector(".clip-title")) el.append(el.querySelector(".clip-title"));
    el.append(quote);
    if (c.descricao) {{
      const d = document.createElement("p");
      d.className = "clip-desc";
      d.textContent = c.descricao + (c.tags.length ? "  \u00b7  " + c.tags.map(t => "#" + t.replaceAll(" ", "")).join(" ") : "");
      el.append(d);
    }}
    el.append(foot);
    return el;
  }}

  function span(cls, text) {{
    const s = document.createElement("span");
    s.className = cls;
    s.textContent = text;
    return s;
  }}

  function setSort(mode) {{
    sort = mode;
    document.getElementById("sort-time").setAttribute("aria-pressed", mode === "time");
    document.getElementById("sort-score").setAttribute("aria-pressed", mode === "score");
    render();
  }}

  document.getElementById("sort-time").onclick = () => setSort("time");
  document.getElementById("sort-score").onclick = () => setSort("score");
  document.getElementById("only-picked").onclick = (e) => {{
    onlyPicked = !onlyPicked;
    e.currentTarget.setAttribute("aria-pressed", String(onlyPicked));
    render();
  }};
  document.getElementById("copy").onclick = async (e) => {{
    const text = CLIPS.filter(c => picked.has(c.id)).map(c => `${{c.id}} (${{c.start}}–${{c.end}})`).join("\\n");
    try {{
      await navigator.clipboard.writeText(text || "nenhum corte marcado");
      e.currentTarget.textContent = "Copiado";
      setTimeout(() => (e.currentTarget.textContent = "Copiar marcados"), 1600);
    }} catch (err) {{
      e.currentTarget.textContent = "Não foi possível copiar";
      setTimeout(() => (e.currentTarget.textContent = "Copiar marcados"), 2400);
    }}
  }};

  render();
</script>
"""


def build_html(
    metadata: dict, *, title: str, clips_dir: str, titles: dict | None = None
) -> str:
    clips = metadata.get("clips", [])
    rendered = [c for c in clips if c.get("rendered")] or clips

    titles = titles or {}
    payload = [
        {
            "id": c["id"],
            "titulo": titles.get(c["id"], {}).get("titulo", ""),
            "descricao": titles.get(c["id"], {}).get("descricao", ""),
            "tags": titles.get(c["id"], {}).get("tags", []),
            "estrela": bool(titles.get(c["id"], {}).get("estrela")),
            "start": c["start"],
            "end": c["end"],
            "seconds": c.get("start_seconds", 0),
            "duration": c["final_duration"],
            "removed": c.get("silence_removed", "0.0s"),
            "score": c.get("importance_score", 0.0),
            "text": c.get("transcription", ""),
        }
        for c in rendered
    ]

    total_removed = sum(_seconds(c.get("silence_removed", "0")) for c in rendered)
    total_final = sum(_seconds(c.get("final_duration", "0")) for c in rendered)

    stats = "".join(
        f'<div class="stat"><b>{value}</b><span>{label}</span></div>'
        for value, label in (
            (len(payload), "cortes"),
            (f"{total_final / 60:.0f} min", "material"),
            (f"{total_removed:.0f}s", "pausa cortada"),
            (metadata.get("total_duration", "—"), "original"),
        )
    )

    return TEMPLATE.format(
        title=title,
        eyebrow="Revisão de cortes",
        heading=title,
        subtitle=(
            f"{len(payload)} cortes de {metadata.get('source', 'vídeo')}, "
            f"transcritos com {metadata.get('transcription_engine', '—')}. "
            "Marque os que vão para publicação."
        ),
        stats=stats,
        clips_dir=clips_dir,
        clips_json=_safe_json(payload),
        storage_key=metadata.get("source", "clips").replace(" ", "_"),
    )


def _safe_json(payload: object) -> str:
    """JSON seguro para embutir dentro de <script>.

    json.dumps não escapa `<`, então uma transcrição contendo "</script>" fecharia o
    bloco e o resto do texto viraria HTML.
    """
    raw = json.dumps(payload, ensure_ascii=False)
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _seconds(value: str) -> float:
    try:
        return float(str(value).rstrip("s"))
    except ValueError:
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Relatório HTML de revisão dos cortes.")
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", default="Revisão de cortes")
    parser.add_argument("--clips-dir", default="output/clips/")
    parser.add_argument("--titles", help="JSON de títulos/descrições por clip (titulos.json)")
    args = parser.parse_args()

    metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
    titles = (
        json.loads(Path(args.titles).read_text(encoding="utf-8")).get("clips", {})
        if args.titles
        else None
    )
    html = build_html(metadata, title=args.title, clips_dir=args.clips_dir, titles=titles)
    Path(args.output).write_text(html, encoding="utf-8")
    print(f"[podcast-clipper] relatório em {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
