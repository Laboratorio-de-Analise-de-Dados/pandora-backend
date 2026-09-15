# BE-20 — Checkpoints de análise: marcos nomeados e restauração por ponto

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/analysis-checkpoints`
**Status:** não iniciado — depende de [ADR-0017](../adr/0017-checkpoints-de-analise.md) (Proposto).
**ADRs relacionados:** [0008](../adr/0008-historico-append-only-de-analise.md) (log append-only), [0016](../adr/0016-gate-nao-avaliavel-sem-canal.md), [0017](../adr/0017-checkpoints-de-analise.md). Ponte futura: [BE-19](BE-19-workspaces-templates-analise.md).

## Problema

O histórico (BE-08) registra cada operação isolada. O uso real é por sessão:
o usuário faz uma sequência de edições e quer um ponto de retorno nomeado —
"antes do reprocessamento", "gates de CD4 conferidos" — para o qual possa
voltar de uma vez, com mensagem, como um commit. Hoje voltar exigiria
reverter revisão por revisão, na mão, na ordem certa.

## Escopo

### 1. Modelo `AnalysisCheckpoint`

Entidade de primeira classe (ADR-0017):

```
AnalysisCheckpoint
  experiment   FK → ExperimentModel (related_name="checkpoints")
  revision     FK → AnalysisRevision  (o ponto marcado — última revisão
               do momento da criação)
  message      CharField (opcional, branco = nome automático
               "Checkpoint <data/hora>")
  created_by   FK → User (SET_NULL)
  created_at   DateTimeField
```

Índice `(experiment, -created_at)`. Sem edição e sem delete físico — coerente
com ADR-0005/0008; "descartar" um checkpoint, se necessário, é `active=false`
como nos demais modelos.

### 2. Endpoints

```
POST /analytics/experiment/<id>/checkpoints/          { message? }
GET  /analytics/experiment/<id>/checkpoints/          lista (id, message, author, created_at, revision_id)
POST /analytics/experiment/<id>/checkpoints/<cp>/restore/   { dry_run?, force? }
```

- Criar: resolve a última `AnalysisRevision` do experimento (ou a revisão
  indicada, se o front quiser marcar um ponto retroativo — parâmetro
  opcional `revision_id`) e grava o checkpoint.
- Restore `dry_run`: percorre as revisões posteriores a `checkpoint.revision`
  em ordem inversa, compõe o plano reusando `plan_revert` por revisão e
  devolve `would_change` + `conflicts` agregados — mesma ergonomia do
  revert unitário.
- Restore real: **atômico** — qualquer conflito → `409` com a lista, nada
  aplicado. `force=true` sobrescreve os conflitos e restaura o estado do
  checkpoint; o front nomeia isso "Sobrescrever alterações".
- Sucesso grava **uma** revisão nova (`action="restore"`, `reverts` →
  `checkpoint.revision`, `payload_before` com o antes de cada operação) —
  o log continua append-only e legível.

### 3. Recriação com recálculo

Gates recriados pelo restore (revert de delete) nascem com ids novos
(`id_map` remapeia a subárvore; `copied_from` externo já consumido por
`SET_NULL` não volta — decisão do ADR-0017). O restore dispara
`recalculate_gate_analysis` sobre os gates recriados — stats voltam
calculadas, incluindo o marcador `applicable: false` do ADR-0016 quando o
canal não existir na amostra.

### 4. Permissões

- Leitura (histórico + checkpoints): quem enxerga o experimento.
- Criar checkpoint e restaurar: `can_edit_experiment` (editor/dono/admin).

## Arquivos a tocar

- `analytics/models.py` — `AnalysisCheckpoint` + `ACTION_RESTORE` em
  `AnalysisRevision`.
- `analytics/history.py` — `plan_restore(checkpoint)` (compõe `plan_revert`
  em cadeia) + `apply_restore` (executa, grava revisão composta, dispara
  recálculo dos gates recriados).
- `analytics/serializers.py` — create/list/restore; validação no serializer
  (ADR-0009).
- `analytics/views.py` + `urls.py` — os três endpoints, queryset escopado
  (ADR-0014) e `@extend_schema`.
- `analytics/migrations/` — nova migration.
- `analytics/tests.py` — casos abaixo.

## Critérios de aceite

- [ ] `POST /checkpoints/` cria marco com mensagem opcional; lista ordenada
  por data desc.
- [ ] Restore `dry_run` devolve plano composto + conflitos sem tocar o banco.
- [ ] Restore com conflito → `409` com a lista de conflitos, nada aplicado.
- [ ] Restore `force=true` restaura o estado do checkpoint sobre conflitos e
  grava revisão `action="restore"` com `reverts` apontando ao marco.
- [ ] Restore sem conflito devolve a árvore ao estado do checkpoint
  (edições posteriores desfeitas; gates deletados recriados).
- [ ] Gates recriados têm `AnalysisResult` recalculado (incluindo
  `applicable: false` + `missing_channels` quando couber).
- [ ] O log mostra a restauração como um evento único legível; o histórico
  permanece append-only.
- [ ] Quem só lê o experimento não cria checkpoint nem restaura (403).

## Fora de escopo

- UI — FE-25.
- Promover checkpoint a template/workspace — BE-19 (o modelo já está em
  forma compatível; a funcionalidade não é prometida aqui).
- Expurgo ou edição de checkpoints.
- Colaboração em tempo real — o log dá rastreabilidade por autor, não
  presença/merge ao vivo.
