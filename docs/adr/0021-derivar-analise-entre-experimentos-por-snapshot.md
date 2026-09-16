# ADR-0021 — Derivar análise entre experimentos por snapshot, sem vínculo vivo

- **Status:** Proposto
- **Data:** 2026-09-16
- **Contexto do código:** `fcs_parser/services/copy_experiment.py`
  (`_copy_analysis` clona árvore entre amostras), `analytics/history.py`
  (revisões append-only, revert de `create`), `fcs_parser/models.py`
  (`content_guid`/`content_sha256` — ADR-0012), `analytics/models.py`
  (`GateModel`, `CompensationMatrix`)

## Contexto

O caso real (BE-19): dois experimentos com a mesma aquisição/painel no
mesmo workspace — o usuário quer "criar uma análise a partir da análise
recebida" sem refazer o gating. Hoje a única portabilidade é
`ExperimentCopyView`, que clona o experimento **inteiro** (blob,
amostras, análise) — não serve quando o experimento alvo já existe com
amostras próprias e se quer só a estratégia.

## Decisão

`POST /experiment/<target>/derive-analysis/` deriva a estratégia de um
experimento origem **por snapshot** — sem objeto template e sem vínculo
vivo:

- **Match de amostras**: `content_guid` (identidade de conteúdo,
  ADR-0012); fallback `file_name` exato. O que não casa é reportado, não
  adivinhado.
- **Cópia**: a árvore de gates da amostra casada é clonada para a amostra
  alvo — mesma lógica de `_copy_analysis` (pais antes de filhos,
  `plot_config` e `color` preservados), **sem `copied_from`**: a linhagem
  por `copied_from` é intra-experimento (família de cópias para
  edição/apply em conjunto, ADR-0003) — cruzar experimentos faria um
  apply "família" tocar gates de outro experimento. A proveniência fica
  na revisão (`payload` + `summary` citam a origem).
- **Amostra com gates existentes é pulada**, não mesclada — merge de
  análises é o BE-23, não aqui.
- **Subsamples** (`include_subsamples`, default true): amostras casadas
  são encaixadas em subsamples de mesmo nome da origem (criados se
  faltarem), gravando `move_subsample` por amostra.
- **Compensação** (`include_compensation`, default true): a matriz
  aplicada da origem é clonada para o alvo e aplicada — a derivação leva
  a análise completa, não só a geometria.
- **Auditoria**: uma revisão `create` por amostra (revertível — apaga os
  gates criados) + uma revisão `derive` no experimento como marco
  resumo. Canais ausentes no alvo não bloqueiam: o gate copiado fica
  não-avaliável (ADR-0016), sinalizando "configurado para outro painel".
- **Permissão**: `can_edit_experiment` na origem **e** no alvo — mesmo
  zero trust do `ExperimentCopyView` (viewer não exporta estratégia).

## Alternativas consideradas

### A) `AnalysisTemplate`/`Workspace` como objeto persistido

O template vira entidade própria e experimentos "instanciam" dele.
Descartada para a v1: cria um terceiro objeto de domínio (template ↔
experimento ↔ branch) antes do caso de uso justificar — o laboratório
quer derivar de uma análise *existente*, não manter um catálogo de
templates. Se a necessidade aparecer, a derivação por snapshot já grava
a proveniência para migrar.

### B) Vínculo vivo (mudança na origem propaga para derivados)

Rejeitada: exige `derived_from` por gate + política de propagação/
conflito idêntica ao merge do BE-23 — complexidade de branch sem o
benefício (experimentos divergem naturalmente: re-gates por lote).

### C) `copied_from` cruzando experimentos

Rejeitada: `copied_from` define a família de cópias usada por edição e
apply em escopo "família" (ADR-0003); um elo cross-experimento faria uma
edição de família tocar o experimento alheio.

## Consequências

- Ganha-se: "análise a partir da recebida" em uma chamada, auditável e
  revertível por amostra; painel divergente sinalizado, não silencioso.
- Dívida: derivações não se atualizam quando a origem evolui (snapshot —
  decisão consciente); match por `file_name` é sensível a renomeio de
  arquivo na origem (guid cobre o caso forte).
- Match de amostras ignora `source_path` como critério próprio — o guid
  já é identidade de conteúdo; `source_path` muda entre uploads mesmo
  com conteúdo igual.
