# BE-26 — Identificação de amostras controle pelo usuário

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/control-marking`
**Status:** não iniciado — estende ADR-0019 (`control_type`/`control_channel`).

## Problema

A frente "clusterizar o controle → derivar gates" (juvia ADR-0003) exige
saber qual amostra é o controle. Não existe sinal determinístico no FCS
— nome de arquivo e keywords de header são convenções falíveis. A
informação tem que vir do usuário, mas pedir no upload bloqueia um fluxo
bulk e o usuário pode ainda não saber qual análise vai rodar.

## Escopo

### 1. Marcação editável a qualquer momento

- Estender o mecanismo de controles (ADR-0019): `control_type` passa a
  cobrir os tipos que a derivação de gates precisa distinguir —
  `compensation` (single-stain, ADR-0019), `fmo`, `isotype`, `unstained`
  e `biological` (referência). Gate de positividade usa FMO/isotípico/
  unstained; usar o tipo errado seria erro silencioso
- Marcação por amostra/subsample, PATCH a qualquer momento — **não
  bloqueia upload**

### 2. Sugestão assistida

- Heurística por filename/keywords (`unstained`, `fmo`, `neg`,
  `control`, `comp`, `beads`) pré-sugere `control_type` provável na
  resposta de listagem (campo `suggested_control`) — usuário confirma,
  nunca é aplicado sozinho
- O front exibe a sugestão para confirmação em lote (FE-xx à parte)

## Arquivos a tocar

- `fcs_parser/models.py` — extensão de `control_type` (+migration se
  necessário)
- `fcs_parser/serializers.py` / `views.py` — PATCH de marcação,
  `suggested_control` na listagem
- `fcs_parser/services/` — heurística de sugestão (função pura, testável)

## Critérios de aceite

- [ ] Usuário marca/desmarca controle (com tipo) fora do fluxo de upload
- [ ] Listagem traz `suggested_control` por heurística — informativo,
      nunca aplicado
- [ ] `control_type` distingue compensação × referência biológica

## Fora de escopo

- Detecção estatística automática de controles (ML sugerindo) — fase
  posterior quando houver dados
- O consumo do controle pelo Juvia para derivar gates — juvia JV-03
