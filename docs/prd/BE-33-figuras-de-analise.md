# BE-33 — Figuras de análise persistidas (spec + cache + procedência)

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/analysis-figures`
**Status:** não iniciado.
**Dependências conceituais:** BE-08/BE-20 (`AnalysisRevision` como âncora
de procedência), BE-22 (stats por população), FE-36 (consumidora).
**Decisão 2026-09:** a figura é um artefato **persistido e auditável** —
spec + cache regenerável + revisão de origem — porque o requisito de
produto é "exportar e confiar nos dados". Grupos de réplicas vivem no
spec da figura (decisão por figura); subsamples servem só de preset na
UI, nunca de binding no modelo.
**Decisão 2026-09 (rev.2):** `result_cache` é **efêmero e regenerável**
— mesma filosofia do Parquet (L2, ADR-0004): guardamos a *receita*
(spec) e a *âncora* (revisão), não o produto pronto. Sem tabela de
histórico de resultados — o log de revisões já registra o que mudou e
`revert` cobre o "deu diferente". Staleness por **fingerprint** (só os
alvos resolvidos da figura), `published` trava a figura no v1, e
recompute reporta o que deixou de resolver.

## Problema

O citometrista monta hoje o gráfico comparativo fora do app (Prism/
Excel) — e quando volta, não há como responder "esses dados vieram de
qual estado da análise?". Uma figura persistida resolve a galeria; uma
figura **com procedência** resolve a confiança: re-gating posterior não
pode corromper silenciosamente a figura que vai pro relatório.

## Escopo

### 1. Modelo `AnalysisFigure` (app `analytics`)

```python
experiment      FK Experiment (scoping via experiment)
branch          FK AnalysisBranch null — figura pode ser de uma linha
name            CharField(120)
chart_type      CharField — "stats_bar" | "stats_strip" | "distribution"
spec            JSONField — populações, métrica, canal, grupos (ver §3)
result_cache    JSONField null — resultado + fingerprint (ver §4)
result_revision FK AnalysisRevision null — âncora de procedência
published       BooleanField default False — trava spec/recompute (§6)
created_by      FK User null | created_at | updated_at | active
```

- `active` = soft delete (regra da casa — a API nunca deleta).
- `result_revision` referencia a revisão head do experimento (na branch,
  quando setada) **no momento do cômputo** — é o carimbo de procedência,
  não um vínculo que bloqueia a análise.
- **Sem tabela de histórico de resultados.** O cache é derivado: a
  receita é o `spec`, a trilha de mudança dos dados é o
  `AnalysisRevision` (que já registra quem mudou, o quê, e reverte).
  Guardar versões de resultado seria duplicar o que a timeline já faz.

### 2. Endpoints (app `analytics`, padrão dos demais)

| Ação | Endpoint |
|---|---|
| Listar | `GET /analytics/experiment/<id>/figures/` → `[{id, name, chart_type, updated_at, is_stale, published, created_by_name}]` |
| Criar | `POST /analytics/experiment/<id>/figures/` `{name, chart_type, spec, branch_id?}` → 201 com `result_cache` computado |
| Detalhe | `GET /analytics/figures/<id>/` → `{..., spec, result_cache, result_revision, is_stale}` |
| Editar | `PATCH /analytics/figures/<id>/` — `name`/`spec`/`published`; mudar `spec` não regrava cache mudo — resposta sinaliza `stale` |
| Arquivar | `DELETE /analytics/figures/<id>/` → `active=false` |
| Recomputar | `POST /analytics/figures/<id>/recompute/` → recalcula `result_cache`, move `result_revision` para a head atual, devolve diff (ver §5) |

Escopo de leitura via `experiments_visible_to(user)`; escrita via
`can_edit_experiment`. Busca por id sempre escopada — nunca
`objects.get(id=...)` cru.

### 3. `spec` — contrato por tipo

```jsonc
{
  // comum
  "groups": [
    { "name": "Controle D0", "file_data_ids": [1, 2] },
    { "name": "Tratado D7", "file_data_ids": [5, 6] }
  ],
  "populations": ["Lymphocytes/CD3/CD4", "Lymphocytes/CD3/CD8"], // caminhos de nomes
  "metric": "percent_parent", // | percent_total | mean_mfi | median_mfi | std_dev | cv
  "channel": "PE-A",          // obrigatório só para métricas *_mfi/std/cv

  // só chart_type="distribution": população única + canal; grupos viram
  // as séries sobrepostas (amostras ou gates dos grupos)
}
```

Validação no serializer (ADR-0009):

- `file_data_ids` devem ser `FileData` ativas do experimento; id alheio
  ou inexistente → 400 com `detail`.
- `populations` não vazias; `chart_type` no enum; `channel` exigido só
  quando a métrica depende de canal.
- Nome duplicado no experimento → 409 (mesmo padrão de branch).
- `PATCH` com `updated_at` do cliente divergente → 412 (optimistic
  locking simples — evita sobrescrever edição concorrente).

### 4. Cômputo, fingerprint e procedência

`compute_figure(figure)` (em `analytics/services/figures.py`):

- Resolve as populações por caminho de nomes em cada amostra dos grupos
  (mesma regra de casamento do `derive_analysis`/goToAdjacent), puxa a
  stats (`analysis_result` do gate; stats de raiz para `file`) e monta
  o `result_cache`:

```jsonc
{
  "rows": [ { "group", "file_data_id", "file_name", "population", "value" } ],
  "resolved_inputs": { "gate_ids": [..], "file_data_ids": [..], "channel": "PE-A" },
  "unmatched": { "populations": ["CD4"], "files": [7] },   // ver §5
  "meta": { "n_por_grupo": {"Controle": 3}, "computed_at": "…" }
}
```

- `resolved_inputs` é o **fingerprint**: `is_stale` = existe
  `AnalysisRevision` mais recente que `result_revision` cujo
  `target_type`/`target_id` (ou `affected_ids`) intersecta o conjunto
  resolvido — **ou** ação experiment-wide (`apply`, `merge`, `derive`,
  `compensation_apply`, `compensation_remove`, `move_subsample`), que
  pode mudar stats de qualquer gate. Renomear um gate que a figura não
  usa não marca stale.
- `result_revision` = última revisão do experimento naquela branch.
- População não-avaliável numa amostra → linha **ausente** no cache
  (nunca zero) — mesma regra do export do front (`buildAnalysisRows`).
- `chart_type="distribution"`: o cache guarda só os metadados resolvidos
  (`resolved_inputs` + `unmatched`) — as curvas vêm do `densityService`
  live; a âncora de procedência continua valendo.

### 5. O assassino silencioso — `unmatched` + diff do recompute

Gate renomeado quebra o caminho do spec; amostra arquivada sai do grupo.
O recompute **não pode sumir com a série sem aviso**:

- `unmatched` no cache lista populações/arquivos que falharam a
  resolução — visível no detalhe da figura ("P1.Q1 não resolve mais").
- Resposta do `recompute/` inclui `removed_since_last`: o que estava
  resolvido no cache anterior e deixou de estar — o front confirma
  antes de o usuário aceitar a figura nova.
- Amostra com `processing_status != "ready"` dentro dos grupos → o
  cômputo roda, mas `meta.warnings` reporta "3 amostras ainda
  processando" — figura incompleta não parece completa.

### 6. `published` — trava para relatório

- `published=true` → `PATCH` de `spec` e `recompute/` retornam 409
  `{"detail": "Figura publicada — despublique para alterar."}`.
- Toggle `published` permitido a quem tem `can_edit_experiment` (não é
  assinatura legal — é proteção contra mudança acidental).
- Figura publicada continua listando/exportando normalmente; a trava é
  só de mutação.

### 7. O que não vira revisão

CRUD de figura **não** grava `AnalysisRevision` — a timeline é da
estratégia de análise; figura é artefato de relatório. Recompute só move
a âncora `result_revision`. (Registrado para não reabrir a discussão.)

## Arquivos a tocar

- `analytics/models.py` — `AnalysisFigure` + migration (expand-only).
- `analytics/serializers.py` — `AnalysisFigureSerializer` +
  `FigureSpecSerializer` (validação do §3).
- `analytics/services/figures.py` — `compute_figure`, resolução de
  população por caminho, fingerprint/`is_stale`, diff de `unmatched`.
- `analytics/views.py` + `analytics/urls.py` — endpoints do §2.
- `analytics/tests.py` — casos abaixo.

## Critérios de aceite

- [ ] Criar figura computa `result_cache` e carimba a revisão head
- [ ] `file_data_id` de outro experimento ou inexistente → 400
- [ ] Amostra sem a população → ausente no cache (não zero)
- [ ] Editar gate **da figura** → `is_stale=true`; editar gate não
      relacionado → `is_stale` continua `false` (fingerprint)
- [ ] `compensation_apply`/`merge`/`apply` marcam stale em qualquer
      figura do escopo
- [ ] Recompute devolve `removed_since_last` quando resolução quebra
- [ ] Amostra em processamento dentro do grupo → `meta.warnings`
- [ ] `published` bloqueia PATCH de spec e recompute (409) até destravar
- [ ] `PATCH` com `updated_at` divergente → 412
- [ ] Leitor sem permissão de edição lê a figura, não edita/recomputa
- [ ] Testes: create, validação de spec, fingerprint, unmatched diff,
      published, optimistic lock, soft delete, escopo de permissão

## Fora de escopo

- Testes estatísticos (ANOVA e afins) — a `result_cache.rows` é a
  entrada natural para eles; PRD próprio (fase 2 do FE-36).
- Figuras cross-experimento — `spec.groups` só aceita `file_data_ids`
  do próprio experimento (identidade da população via derivação é
  pré-requisito para abrir isso).
- Export da imagem — front (Plotly); backend só fornece os dados.
- Histórico de resultados da figura — decisão registrada (rev.2): cache
  regenerável + log de revisões cobrem a trilha; não criar tabela de
  versões de resultado.
