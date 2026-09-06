# 🎙️ Podcast Clipper Skill

Automatiza a extração de trechos de podcasts/entrevistas em vídeo. Transcreve local
e offline, identifica pausas naturais, remove silêncios e gera clips prontos para
redes sociais.

## ⚡ Quick Start

1. Instale as dependências:
   ```bash
   brew install ffmpeg
   pip3 install faster-whisper
   ```
2. Peça na sessão do Claude Code:
   ```
   Usa a skill podcast-clipper pra processar esse vídeo.
   Extrai os trechos (até 1min), remove pausas > 2s.
   ```
3. Receba os clips em `output/clips/` e os metadados em `output/metadata.json`.

Ou rode direto:

```bash
cd ~/.claude/skills/podcast-clipper/scripts
python3 clipper.py --source "/caminho/entrevista.mp4" --output-dir "/caminho/output"
```

## 🎯 O que faz

- Converte a entrada para MP4 (HEIC/MOV/MKV/HEVC)
- Extrai áudio WAV mono 16 kHz
- Transcreve local (Meetily/whisper.cpp, faster-whisper, openai-whisper)
- Detecta pausas/silêncios acima do threshold
- Corta nas pausas, respeitando duração mínima e máxima
- Remove os silêncios longos **dentro** de cada clip
- Gera os MP4s finais e um `metadata.json` com transcrição, timecodes e scores

## 📦 Dependências

| Dependência | Obrigatória | Instalação |
|-------------|-------------|------------|
| ffmpeg + ffprobe | Sim | `brew install ffmpeg` |
| faster-whisper | Sim (ou outro engine) | `pip3 install faster-whisper` |
| Meetily backend | Opcional | app Meetily rodando em `MEETILY_SERVER_URL` |
| whisper.cpp CLI | Opcional | `brew install whisper-cpp` + `WHISPER_CPP_MODEL` |

> **Nota sobre o Meetily:** não existe `pip install meetily`. O Meetily é um app de
> notas de reunião cujo backend expõe um servidor whisper.cpp em `POST /inference`.
> A skill fala com esse endpoint quando ele está no ar; caso contrário cai
> automaticamente para o faster-whisper. O engine efetivamente usado aparece em
> `metadata.json` → `transcription_engine`.

## 🔧 Parâmetros padrão

| Parâmetro | Flag | Valor |
|-----------|------|-------|
| Duração mínima do clip | `--min-clip-duration` | 2 s |
| Duração máxima do clip | `--max-clip-duration` | 60 s |
| Pausa que marca corte | `--silence-threshold` | 2 s |
| Nível de silêncio | `--silence-db` | -40 dB |
| Pausa removida no clip | `--silence-removal` | 0,8 s |
| Respiro nas bordas | `--edge-padding` | 0.15 s |
| Teto de clips | `--max-clips` | 60 |
| Lado menor da saída | `--scale-short-side` | 1080 px (`0` = original) |
| Encoder de vídeo | `--video-encoder` | auto (libx264) |
| Renders simultâneos | `--jobs` | 0 (deriva dos núcleos) |
| Legendas .srt | `--no-subtitles` desliga | ligadas |
| Idioma | `--language` | pt |

## 📂 Estrutura de output

```
output/
├── clips/
│   ├── clip_001.mp4
│   ├── clip_001.srt
│   └── ...
├── transcript.json     # cache da transcrição — não apagar
└── metadata.json
```

### metadata.json

```json
{
  "source": "entrevista.mp4",
  "total_duration": "01:23:45",
  "clips_generated": 15,
  "clips_planned": 15,
  "transcription_engine": "faster-whisper",
  "language": "pt",
  "parameters": { "silence_split_threshold_s": 2.0, "...": "..." },
  "full_transcription": "Então começou tudo quando...",
  "clips": [
    {
      "id": "clip_001",
      "filename": "clip_001.mp4",
      "rendered": true,
      "start": "00:12",
      "end": "00:34",
      "start_seconds": 12.4,
      "end_seconds": 34.4,
      "original_duration": "22.0s",
      "silence_removed": "2.1s",
      "final_duration": "19.9s",
      "transcription": "Então começou tudo quando a gente...",
      "importance_score": 8.5
    }
  ]
}
```

`importance_score` (0–10) é heurístico e determinístico: densidade de fala,
proximidade da duração ideal (~35 s) e presença de marcadores de gancho. Use para
ranquear, não como decisão final.

## 💡 Fluxo recomendado

1. `--dry-run` para planejar sem renderizar (rápido).
2. Revise `metadata.json` e ajuste os parâmetros.
3. Rode de novo sem `--dry-run` para gerar os MP4s.

## 🔍 Troubleshooting

| Sintoma | Ação |
|---------|------|
| `'ffmpeg' não encontrado` | `brew install ffmpeg` |
| `Nenhum engine de transcrição disponível` | `pip3 install faster-whisper` |
| Poucos clips gerados | `--silence-threshold 1.5` (ou `1.0`) |
| Clips muito curtos | `--min-clip-duration 8` |
| Clips cortando a fala | `--edge-padding 0.3` |
| `não contém stream de vídeo` | O `.HEIC` é foto, não vídeo — use o `.MOV` |
| Transcrição muito lenta | `--whisper-model small` |
| Render muito lento (4K) | suba `--jobs`; o gargalo é decodificar o codec de origem |
| `silence_removed` sempre 0s | `--silence-removal` precisa ser menor que `--silence-threshold` |
| Vídeo vertical ficou estreito | use `--scale-short-side`, nunca escale por altura |

## 🧪 Testes

```bash
cd ~/.claude/skills/podcast-clipper
python3 -m unittest discover -s tests -t .
```

62 testes cobrindo parsing de silêncio, montagem de clips, pontos de corte, remoção
de pausas, filtros de render, escolha de encoder, legendas, scoring e validação. Não dependem de ffmpeg nem de modelos.

## 🗂️ Arquivos

```
podcast-clipper/
├── SKILL.md              # instruções carregadas pelo Claude Code
├── README.md
├── requirements.txt
├── scripts/
│   ├── clipper.py        # CLI + orquestração
│   ├── config.py         # parâmetros e ClipperConfig (imutável)
│   ├── ffmpeg_utils.py   # wrappers de ffmpeg/ffprobe
│   ├── media.py          # normalização de vídeo + extração de áudio
│   ├── silence.py        # silencedetect
│   ├── transcribe.py     # engines de transcrição com fallback
│   ├── cutpoints.py      # pontos de corte em fim de frase
│   ├── segment.py        # montagem e scoring dos clips
│   ├── subtitles.py      # geração dos .srt
│   ├── transcript_cache.py  # cache da transcrição
│   └── render.py         # render final com remoção de silêncio
└── tests/
    ├── test_pipeline.py
    └── test_subtitles.py
```

---

**Versão**: 1.0 · **Python**: 3.8+ · **Licença**: MIT
