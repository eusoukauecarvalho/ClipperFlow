---
name: podcast-clipper
description: Extrai clips curtos de podcasts e entrevistas em vídeo. Transcreve local e offline (faster-whisper/Meetily), detecta pausas naturais, corta em fim de frase, remove silêncios internos, gera MP4 vertical ou horizontal com .srt ao lado e metadata.json com transcrição, timecodes e scores. Use quando o usuário quiser cortar podcast, gerar cortes/clips de entrevista, extrair trechos de vídeo longo, legendar cortes ou transcrever e fatiar gravações.
---

# Podcast Clipper

Vídeo longo → clips curtos legendados, com transcrição local e cortes em fim de frase.

## Quando ativar

- "cortar esse podcast", "gerar cortes", "extrair os melhores trechos"
- Entrevista/gravação longa que precisa virar Reels/Shorts/TikTok
- Transcrever uma gravação e fatiar por pausas
- Legendar cortes já existentes
- O usuário aponta um `.mp4`/`.mov`/`.HEIC` e pede clips

## Pipeline

1. **Normaliza** a entrada para MP4 (`media.normalize_video`). MOV/MP4/M4V passam
   direto, sem re-encode — uma geração de perda a menos.
2. **Extrai áudio** WAV mono 16 kHz (`media.extract_audio`).
3. **Detecta silêncios** com `silencedetect` (`silence.detect_silences`).
4. **Transcreve** local e offline, com cache em disco (`transcript_cache`).
4b. **Diariza** (opcional, essencial em podcast): atribui um locutor a cada fala
    (`diarize`) e remove dos clips os turnos de quem NÃO está na câmera (`persona`).
5. **Monta os clips** cortando em fim de frase (`cutpoints` + `segment.build_clips`).
6. **Renderiza** em paralelo, removendo silêncios internos (`render.render_clip`).
7. **Escreve** um `.srt` por clip (`subtitles`) e o `metadata.json`.
8. **Titula e renomeia** para publicação (`titling`): títulos SEO escritos a partir da
   transcrição, arquivos renomeados como `NNN - Título.mp4`.

## Como executar

```bash
cd ~/.claude/skills/podcast-clipper/scripts
python3 clipper.py --source "/caminho/entrevista.mov" --output-dir "/caminho/output"
```

Rode a partir de `scripts/` (os módulos se importam por nome) ou use
`PYTHONPATH=~/.claude/skills/podcast-clipper/scripts`.

**Sempre comece com `--dry-run`**: planeja os cortes e escreve `metadata.json` sem
renderizar. Como a transcrição fica em cache, ajustar parâmetros depois custa segundos.

## Parâmetros

| Flag | Padrão | Descrição |
|------|--------|-----------|
| `--min-clip-duration` | `2` | Duração mínima do clip (s) |
| `--max-clip-duration` | `60` | Duração máxima do clip (s) |
| `--silence-threshold` | `2` | Pausa que marca fronteira de corte (s) |
| `--silence-db` | `-40` | Nível considerado silêncio (dB) |
| `--silence-removal` | `0.8` | Pausa removida dentro do clip (s) |
| `--edge-padding` | `0.15` | Respiro mantido nas bordas do corte (s) |
| `--max-clips` | `60` | Teto de clips (mantém os de maior score) |
| `--scale-short-side` | `1080` | Lado MENOR da saída em px; `0` mantém o original |
| `--video-encoder` | `auto` | `auto` → libx264; ou qualquer encoder do ffmpeg |
| `--jobs` | `0` | Renders simultâneos; `0` deriva dos núcleos |
| `--no-subtitles` | — | Não gerar os `.srt` |
| `--burn-subtitles` | — | Gravar a legenda na imagem, em `output/legendados/` |
| `--subtitle-font` | — | `.ttf` da legenda queimada |
| `--subtitle-style` | `block` | `block`, `karaoke` ou `pop` |
| `--subtitle-size` | `0.0726` | fonte / largura do vídeo |
| `--signature` | — | PNG fixado no rodapé |
| `--signature-size` | `0.52` | assinatura / largura do vídeo |
| `--zoom` | `0.12` | amplitude; `0` desliga |
| `--zoom-transition` | `4` | segundos de ida (e volta) |
| `--zoom-hold` | `0` | pausa; `0` escolhe pela duração do clip |
| `--language` | `pt` | Idioma da transcrição |
| `--engine` | `auto` | `meetily`, `faster-whisper`, `openai-whisper`, `whisper-cli` |
| `--whisper-model` | `medium` | `small` é ~3× mais rápido |
| `--adjustments` | — | JSON de correções editoriais |
| `--skip-existing` | — | Não re-renderiza MP4 que já existe |
| `--dry-run` | — | Só planeja, não renderiza |
| `--keep-workdir` | — | Mantém áudio e temporários |

### Por que `--silence-removal` (0,8s) é MENOR que `--silence-threshold` (2s)

Não é arbitrário — se os dois forem iguais, a remoção interna **nunca age**. Toda pausa
≥ threshold vira fronteira entre clips, então não sobra pausa ≥ threshold dentro de um
clip. Mantenha `silence-removal` abaixo de `silence-threshold` para que ela faça algo.

### Corte em fim de frase

Quando um bloco passa de `--max-clip-duration`, o divisor não escolhe só a maior pausa:
ele ranqueia candidatos por qualidade editorial (`cutpoints.py`), onde terminar uma
frase (`.`, `!`, `?`) vale mais que uma pausa longa. Sem isso, clips longos são cortados
no limite duro — no meio da frase.

### Resolução: lado menor, não altura

`--scale-short-side 1080` dá 1080×1920 em vídeo vertical e 1920×1080 em horizontal.
Escalar pela *altura* quebraria retrato: um 2160×3840 viraria 608×1080. O filtro usa
`force_original_aspect_ratio=increase` sobre uma caixa quadrada e nunca faz upscale.

### Encoder: por que `auto` é software

Medido em 4K HEVC → 1080p num Apple M5: `h264_videotoolbox` levou 6,75 s e gerou
29,7 MB; `libx264` CRF 20 levou 7,32 s e gerou 3,1 MB. Velocidade empatada porque o
gargalo é **decodificar** o HEVC, não codificar — e o VideoToolbox só aceita bitrate
alvo (rejeita `-q:v`), daí o arquivo ~10× maior. Software vence.

## Legendas

Cada clip sai com um `.srt` ao lado (`clip_007.mp4` + `clip_007.srt`), com os tempos
já compensados pelos silêncios removidos no meio do clip.

### Legenda queimada — `--burn-subtitles`

Gera em `output/legendados/` uma cópia pronta para postar, sem passar por editor.

```bash
python3 clipper.py --source video.mov --output-dir out \
  --burn-subtitles --subtitle-style pop \
  --signature assinatura.png --zoom 0.12
```

**Como funciona sem libass.** O ffmpeg do Homebrew vem sem `libass` e sem `drawtext`,
então `-vf subtitles=...` não existe. Confira com:

```bash
ffmpeg -filters | grep -E "subtitles|drawtext"    # vazio = ausentes
```

`burn.py` contorna isso: cada cartão vira um PNG transparente desenhado com Pillow e
entra por `overlay` com `enable=between(t,...)`, filtro que sempre existe.

### Estilos de legenda — `--subtitle-style`

| Estilo | O que aparece | Precisa de |
|--------|---------------|------------|
| `block` | a frase inteira, estática | transcrição normal |
| `karaoke` | a frase apagada, com a palavra falada acesa | tempo por palavra |
| `pop` | só a palavra (ou grupo) sendo falada | tempo por palavra |

`karaoke` e `pop` ligam `--word-timestamps` sozinhos. Isso **invalida o cache** e força
uma re-transcrição (~2× tempo real) na primeira vez; depois fica cacheado em v2.

**Agrupamento de palavras curtas.** Palavras como "é", "que", "a" duram 0,1–0,2 s e
piscariam na tela. Medido num episódio real: **82% das palavras ficavam abaixo de
0,38 s**. `_merge_short_words` junta cada palavra curta com a seguinte até o grupo
atingir esse mínimo — o índice caiu para 3%, e os grupos leem com ritmo natural
("acho que quando" → "está dando" → "errado é"). Sobra no fim gruda no grupo anterior.

### Assinatura fixa — `--signature`

PNG transparente (foto + @ + selo) fixado no rodapé de todo clip. O espaço vazio é
recortado pelo canal alpha antes de escalar, senão a barra sairia minúscula. A legenda
sobe automaticamente para não encostar nela.

### Zoom — `--zoom`

Aproximação que **entra, para, volta e para de novo**:

```
 4s ida ──╮
          ├── PAUSA ──╮
                       ╰── 4s volta ──╮
                                       ├── PAUSA ──╮ (repete)
```

Um cosseno contínuo nunca descansa e o movimento vira um vaivém perceptível. As
transições usam meia onda de cosseno, então partida e chegada têm velocidade zero.

A pausa acompanha a duração do clip (`--zoom-hold 0`, que é o padrão):

| Duração do clip | Pausa | Ciclo |
|-----------------|-------|-------|
| até 30 s | 5 s | 18 s |
| acima de 30 s | 20 s | 48 s |

**Dois detalhes que não são opcionais:**

1. **Upscale 2× antes do `zoompan`.** O filtro trunca a origem do corte em pixel
   inteiro, o que produz tremor. Medido em fonte estática: irregularidade cai de 1,13
   para 0,49 e o pico de 3,05 para 1,31. Custa ~10 s a mais por clip.
2. **`fps` explícito.** Sem isso o `zoompan` reescreve a cadência para 25 fps e o áudio
   desincroniza.

O zoom entra **antes** das sobreposições: só a imagem respira, legenda e assinatura
ficam paradas.

### Outras rotas

1. **CapCut / Premiere / DaVinci** — importe `.mp4` + `.srt`. Continua sendo a melhor
   opção quando você quer legenda animada ou karaokê palavra a palavra.
2. **Instagram / YouTube / LinkedIn** — aceitam upload do `.srt` direto.

Legenda por palavra exigiria `word_timestamps=True` no faster-whisper, o que invalida
o cache — vale re-transcrever só se esse for o objetivo.

## Seleção semântica (recomendado para qualidade)

A montagem automática corta bem onde o áudio cala, mas **não sabe o que é um pensamento
completo**. Medido neste repo, ela deixa 23% dos cortes terminando em pergunta sem
resposta e 28% abrindo no meio de uma ideia. A seleção semântica troca essa etapa por
julgamento sobre a transcrição:

```bash
# 1. Transcreva (fica em cache) e gere o briefing
python3 clipper.py --source video.mov --output-dir out --dry-run --keep-workdir
python3 -c "..."   # ou use semantic.write_briefing

# 2. LEIA o briefing e escreva a seleção (é aqui que mora o julgamento)
#    O contrato está em semantic.SELECTION_CONTRACT

# 3. Valide e renderize
python3 clipper.py --source video.mov --output-dir out --selection out/selecao.json
```

Resultado medido num episódio de 69 min:

| Método | Cortes | Fecha a ideia | Termina em "?" | Tem CTA |
|--------|--------|---------------|----------------|---------|
| Heurística acústica | 60 | 75% | 23% | 3% |
| Seleção semântica | 38 | **87%** | **11%** | **0%** |

A seleção é **sempre validada** contra a transcrição: timestamps inventados, janelas
invertidas ou fora dos limites de duração são recusados, e todo limite é encaixado na
fronteira de fala mais próxima.

**Não pule a revisão humana.** Na comparação acima, a seleção semântica descartou 7
trechos que o dono do conteúdo quis manter — material de trajetória que ela julgou
menos "viralizável". Ela reduz o trabalho de revisão; não o substitui.

## Ajustes editoriais

`--adjustments plano.json` aplica correções por cima dos cortes automáticos: estender,
encurtar, absorver o clip seguinte (`absorb`) ou remover. Serve para o dono do conteúdo
corrigir sem reprocessar nada — a transcrição vem do cache e o replanejamento leva
segundos. Só os clips alterados precisam de novo render (`--skip-existing`).

## Persona: cortar só quem está na câmera

Num podcast, cada arquivo de vídeo é a câmera de UMA pessoa, mas o áudio tem todas.
Sem diarização, saem clips do convidado calado ouvindo a pergunta do apresentador.

```bash
# 1. Identificar quem fala quando (uma vez por episódio; ~minutos em CPU)
python3 diarize.py --audio out/.work/audio.wav --transcript out/transcript.json \
  --output out/speakers.json --speakers 2

# 2. O diarize imprime AMOSTRAS de fala de cada locutor (S1, S2...) — identifique
#    com o usuário qual é a persona da câmera. Não adivinhe sozinho.

# 3. Cortar removendo os turnos dos outros locutores
python3 clipper.py ... --speakers out/speakers.json --persona S1
```

Como funciona: os turnos de outros locutores viram trechos removíveis dentro de cada
clip — o mesmo mecanismo da remoção de silêncio, então render, metadata e legendas
ficam consistentes de graça. Duas decisões deliberadas:

- **Reações curtas ficam** (< 1,6 s): remover cada "uhum"/"caramba" criaria um jump
  cut por reação. Só turnos longos (pergunta inteira) são removidos.
- **Turnos vizinhos do outro locutor viram um corte só** — menos emendas no vídeo.

Dependências: `pip3 install speechbrain scipy` (modelo ECAPA-TDNN, roda local em CPU;
o WAV é lido com a stdlib — não precisa de torchcodec).

Limite honesto: a diarização por voz erra em sobreposição de falas e em locutores de
timbre muito parecido. Confira o resumo de tempo por locutor que o `diarize` imprime —
num podcast de entrevista, o convidado fala bem mais que o apresentador.

## Títulos e renomeação para publicação

Depois que os clips finais existem, a última etapa transforma nomes técnicos em nomes
de publicação:

1. **Escrever os títulos** — etapa de JULGAMENTO, não de script: leia a transcrição de
   cada clip no `metadata.json` e escreva `titulos.json` seguindo o contrato em
   `titling.TITLES_CONTRACT`. Cada título tem dupla função: gancho (curiosidade,
   contraste, frase dita no clip) + palavra-chave pesquisável. Até 70 caracteres (o
   Google trunca perto disso), caixa normal. Corrija nomes próprios que a transcrição
   errou — nome conhecido no título é palavra-chave forte; na dúvida sobre um nome,
   não chute: pergunte ao usuário (foi assim que "Paulo Massal" virou Pablo Marçal).

2. **Validar e renomear**:

```bash
python3 titling.py --titles out/titulos.json --clips-dir out/legendados --check   # só valida
python3 titling.py --titles out/titulos.json --clips-dir out/legendados          # renomeia
```

`clip_024.mp4` vira `024 - Minha tia dizia que eu terminaria atrás das grades.mp4` —
o prefixo preserva a ordem, acentos ficam, e o mapa `renomeacao.json` guarda o vínculo
id → arquivo. Os clips em `clips/` mantêm os nomes técnicos: são a camada de trabalho;
`legendados/` é a camada de publicação.

3. **Relatório com títulos**: `report.py --titles out/titulos.json` mostra o título,
   a descrição e as hashtags em cada cartão da página de revisão.

## Cache de transcrição

A transcrição é a etapa cara (~2× tempo real: 69 min de áudio ≈ 40 min). O resultado
fica em `output/transcript.json` e é reusado quando idioma e modelo batem. Sem ele,
cada ajuste de corte pagaria a transcrição inteira de novo. **Não apague esse arquivo.**

## Dependências

```bash
brew install ffmpeg          # obrigatório (ffmpeg + ffprobe)
pip3 install faster-whisper  # engine de transcrição recomendado
```

## Saída

```
output/
├── clips/                  # camada de trabalho (nomes técnicos + .srt)
│   ├── clip_001.mp4
│   ├── clip_001.srt
│   └── ...
├── legendados/             # camada de publicação (legenda queimada, renomeados)
│   └── 001 - Título do corte.mp4
├── transcript.json         # cache — não apagar
├── titulos.json            # título + descrição + hashtags por clip
├── renomeacao.json         # mapa clip_id → arquivo publicado
└── metadata.json
```

## Troubleshooting

| Sintoma | Ação |
|---------|------|
| `'ffmpeg' não encontrado` | `brew install ffmpeg` |
| `Nenhum engine de transcrição disponível` | `pip3 install faster-whisper` |
| `silence_removed` sempre `0s` | `--silence-removal` menor que `--silence-threshold` |
| Clips cortados no meio da frase | Já tratado por `cutpoints`; confira se a transcrição tem pontuação |
| Poucos clips | Baixe `--silence-threshold` para `1.5` |
| Clips picotados | Suba `--min-clip-duration` para `8`–`15` |
| Vídeo vertical virou estreito | Use `--scale-short-side`, nunca escale por altura |
| `subtitles` não é um filtro | esperado; use `--burn-subtitles` (não depende de libass) |
| Render lento | Suba `--jobs`; lembre que o gargalo é decodificar o codec de origem |
| Transcrição lenta | `--whisper-model small` |

## Testes

```bash
cd ~/.claude/skills/podcast-clipper && python3 -m unittest discover -s tests -t .
```

154 testes cobrindo parsing de silêncio, montagem de clips, pontos de corte, remoção de
pausas, filtros de render, escolha de encoder, legendas, scoring e validação de config.
Nenhum depende de ffmpeg ou de modelos.
