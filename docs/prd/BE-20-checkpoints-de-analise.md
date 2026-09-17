# BE-20 — Checkpoints de análise: marcos nomeados e restauração por ponto

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/analysis-checkpoints`
**Status:** implementado em `fix/gate-density-missing-channel` (aguardando revisão).
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
  active       BooleanField (soft delete — ADR-0005)
```

Índice `(experiment, -created_at)`. A mensagem é editável via `PATCH`;
"descartar" um checkpoint é `active=false` via `DELETE` — nunca delete
físico (ADR-0005/0008).

### 2. Timeline agrupada em sessões (auto-checkpoints)

`GET /analytics/experiment/<id>/history/` ganha modo agrupado
(`?grouped=1`): as revisões voltam agrupadas em **sessões** — bursts de
atividade separados por inatividade acima de um limiar (15 min por padrão —
env `ANALYSIS_SESSION_GAP_MINUTES`, lida em `analytics/history.py` como
`SESSION_GAP_MINUTES`). Cada grupo expõe
`first_revision_id`, `last_revision_id`, `started_at`, `ended_at`, `count` —
e a borda de cada sessão é um ponto restaurável. É uma **visão derivada na
leitura**: nada é gravado (ADR-0017, alternativa B2 descartada).

### 3. Endpoints de checkpoint e restore

```
POST   /analytics/experiment/<id>/checkpoints/          { message?, revision_id? }
GET    /analytics/experiment/<id>/checkpoints/          lista (id, message, author, created_at, revision_id)
PATCH  /analytics/checkpoints/<cp>/                     { message }
DELETE /analytics/checkpoints/<cp>/                     active=false (soft delete)
POST   /analytics/experiment/<id>/history/<rev>/restore/      { dry_run?, force? }
POST   /analytics/checkpoints/<cp>/restore/             { dry_run?, force? }
GET    /analytics/history/<rev>/state/                  preview: árvore de gates por arquivo na revisão
```

- Criar: sem `revision_id`, marca a última revisão do experimento ("salvar
  ponto agora"); com `revision_id`, fixa aquela borda — é o "pin" de um
  auto-checkpoint ou de uma revisão avulsa.
- **O alvo do restore é sempre uma revisão.** `.../history/<rev>/restore/`
  desfaz todas as revisões posteriores a `rev`; o endpoint de checkpoint é
  a mesma operação apontando para `checkpoint.revision` — um único motor,
  dois nomes de entrada.
- Restore `dry_run`: percorre as revisões posteriores ao alvo em ordem
  inversa, compõe o plano reusando `plan_revert` por revisão e devolve
  `would_change` + `conflicts` agregados — mesma ergonomia do revert
  unitário.
- Restore real: **atômico** — qualquer conflito → `409` com a lista, nada
  aplicado. `force=true` sobrescreve os conflitos e restaura o estado do
  ponto; o front nomeia isso "Sobrescrever alterações".
- Sucesso grava **uma** revisão nova (`action="restore"`, `reverts` → a
  revisão-alvo, `payload_before` com o antes de cada operação) — o log
  continua append-only e legível.

### 4. Recriação com recálculo

Gates recriados pelo restore (revert de delete) nascem com ids novos
(`id_map` remapeia a subárvore; `copied_from` externo já consumido por
`SET_NULL` não volta — decisão do ADR-0017). O restore dispara
`recalculate_gate_analysis` sobre os gates recriados — stats voltam
calculadas, incluindo o marcador `applicable: false` do ADR-0016 quando o
canal não existir na amostra.

### 5. Preview de estado numa revisão

`GET /analytics/history/<rev>/state/` devolve a árvore de gates por arquivo
**como ela era naquela revisão** (`state_at_revision`): snapshot atual menos
o efeito das revisões posteriores, aplicado de forma virtual sobre o dado
em memória — leitura pura, não grava nada. Formato:

```
{ revision_id, files: { "<file_data_id>": [ { id, name, color, parent_id,
  copied_from_id, gate_type, gate_config, ... } ] } }
```

Base do "modo preview" do FE-25 (`👁️ Visualizar`): a primeira versão do
painel mostra antes/depois textual; o preview gráfico consome este endpoint
quando o front estiver pronto.

### 6. Permissões

- Leitura (histórico + checkpoints + preview de estado): quem enxerga o
  experimento.
- Criar/editar/descartar checkpoint e restaurar: `can_edit_experiment`
  (editor/dono/admin).

## Arquivos a tocar

- `analytics/models.py` — `AnalysisCheckpoint` + `ACTION_RESTORE` em
  `AnalysisRevision`.
- `analytics/history.py` — `plan_restore(revision)` (compõe `plan_revert`
  em cadeia) + `apply_restore` (executa, grava revisão composta, dispara
  recálculo dos gates recriados); agrupamento em sessões na leitura do
  histórico (`?grouped=1`).
- `analytics/serializers.py` — create/list/restore; validação no serializer
  (ADR-0009).
- `analytics/views.py` + `urls.py` — os três endpoints, queryset escopado
  (ADR-0014) e `@extend_schema`.
- `analytics/migrations/` — nova migration.
- `analytics/tests.py` — casos abaixo.

## Critérios de aceite

- [ ] `GET .../history/?grouped=1` devolve sessões por janela de atividade
  com bordas (`first`/`last_revision_id`) restauráveis — sem gravar nada.
- [ ] `POST /checkpoints/` cria marco com mensagem opcional; `revision_id`
  opcional fixa uma borda passada (pin de auto-checkpoint); `PATCH` edita a
  mensagem; `DELETE` desativa (sem delete físico).
- [ ] `POST .../history/<rev>/restore/` e `.../checkpoints/<cp>/restore/`
  compartilham o mesmo motor de restore.
- [ ] `GET .../history/<rev>/state/` devolve a árvore por arquivo na revisão,
  sem tocar o banco.
- [ ] Restore `dry_run` devolve plano composto + conflitos sem tocar o banco.
- [ ] Restore com conflito → `409` com a lista de conflitos, nada aplicado.
- [ ] Restore `force=true` restaura o estado do ponto sobre conflitos e
  grava revisão `action="restore"` com `reverts` apontando à revisão-alvo.
- [ ] Restore sem conflito devolve a árvore ao estado do checkpoint
  (edições posteriores desfeitas; gates deletados recriados).
- [ ] Gates recriados têm `AnalysisResult` recalculado (incluindo
  `applicable: false` + `missing_channels` quando couber).
- [ ] O log mostra a restauração como um evento único legível; o histórico
  permanece append-only.
- [ ] Quem só lê o experimento não cria checkpoint nem restaura (403).

## Fora de escopo

- UI — FE-25 (o preview gráfico dela consome o endpoint de estado da seção 5).
- Promover checkpoint a template/workspace — BE-19 (o modelo já está em
  forma compatível; a funcionalidade não é prometida aqui).
- Expurgo de checkpoints inativos (eles só saem da listagem; a linha fica).
- Colaboração em tempo real — o log dá rastreabilidade por autor, não
  presença/merge ao vivo.
