# BE-23 — Análise colaborativa: branches, merge e resolução de conflitos

**Repo:** pandora-backend · **Tipo:** visão (não iniciar sem ADR) · **Base:** `main`
**Status:** visão registrada — ADR-0020 propõe o modelo (branch =
cópias materializadas de gates + merge por diff de árvores); aguarda
aceite para implementar.
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

1. Branch como linhagem de revisões × gates marcados por branch — qual é o
   estado "visível" de uma branch? (Provavelmente: snapshot materializado,
   igual o `state/` do histórico, porém gravável.)
2. O que acontece com `AnalysisResult`/`active` ao trocar de branch —
   recalcular ou cachear por branch?
3. Analisador não-humano (Juvia) pode virar "autor de branch" no futuro —
   o modelo de revisão com `user` nullable já comporta.
