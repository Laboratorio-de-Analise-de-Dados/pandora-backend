# BE-23 — Análise colaborativa: branches, merge e resolução de conflitos

**Repo:** pandora-backend · **Tipo:** backend · **Base:** `main`
**Status:** backend implementado (ADR-0020 aceito) — branches como
cópias materializadas de gates, diff estrutural e merge com resolução
humana de conflitos. Falta a UI (front).
**Contexto:** o modelo de histórico atual (BE-08/BE-20) já é uma engine
estilo git — `AnalysisRevision` append-only ≈ commits,
`AnalysisCheckpoint` ≈ tags, restore ≈ `reset --hard`. Este PRD registra
o que falta para o modelo completo: **duas pessoas analisando em paralelo
e mergeando** o resultado.

## Mapeamento git ↔ modelo atual

| Git | Pandora hoje | Falta para o modelo completo |
|---|---|---|
| commit | `AnalysisRevision` (autor, payload, timestamp) | — |
| tag | `AnalysisCheckpoint` com `message` | — |
| reset/restore | `POST .../history/<rev>/restore/` | — |
| revert | `POST .../history/<rev>/revert/` | — |
| branch | — | `AnalysisBranch` (linha de revisões por usuário/proposta) |
| merge + conflitos | — | diff de árvores de gates + resolução interativa |
| blame | `revision.user` + timeline | — |

## Problema

Hoje a análise é single-head: toda revisão escreve na mesma linha do
experimento. Dois analistas editando gates do mesmo experimento pisam no
trabalho um do outro — a timeline registra, mas não há como isolar
propostas e convergir depois.

## Direção (a detalhar no ADR)

- **`AnalysisBranch`** por experimento: `name`, `created_by`, `base_revision`,
  `head_revision`. Gates carregam `branch` (ou o workspace filtra por
  linhagem de revisões).
- **Diff de branches**: comparar snapshots de árvore (gates criados/
  removidos/editados, compensação aplicada) entre base e head.
- **Merge**: aplicar as revisões da branch filha sobre a principal;
  conflitos = mesmo gate editado nos dois lados (mesma lógica de
  drift/conflict que o revert já usa — `plan_revert` devolve conflitos).
- Revisões já carregam autor — a timeline filtrada por branch mostra "quem
  fez o quê" em cada linha de trabalho.

## Fora de escopo (por ora)

- Lock de edição em tempo real / presença (OT/CRDT é outra conversa).
- Merge automático de geometria de gate (conflito de polígono é humano).

## Decisões em aberto

1. ~~Estado "visível" da branch~~ — **resolvido no ADR-0020**: cópias
   materializadas de gates; branch não é por usuário e não é criada
   automaticamente (explícita, como `git checkout -b`).
2. ~~`AnalysisResult`/`active` por branch~~ — **resolvido no ADR-0020**:
   cache/recalc por `(file, gate, branch, matrix)`; trocar de branch não
   toca resultado de outra.
3. Analisador não-humano (Juvia) pode virar "autor de branch" no futuro —
   o modelo de revisão com `user` nullable já comporta.

## Limite de escopo (registrado 2026-09-16)

"Dois experimentos iguais (mesma aquisição/painel) que deveriam convergir"
**não** é caso deste PRD — é portabilidade de estratégia entre
experimentos, domínio do **BE-19** (templates/cópia de análise; a dedup
por `content_guid`/`sha256` do ADR-0012/0013 já identifica amostras
idênticas). Este PRD cobre apenas linhas paralelas **dentro** de um
experimento.
