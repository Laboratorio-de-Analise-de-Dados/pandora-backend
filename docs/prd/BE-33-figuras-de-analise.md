# BE-33 — Figuras de análise persistidas (spec + cache + procedência)

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/analysis-figures`
**Status:** não iniciado.
**Dependências conceituais:** BE-08/BE-20 (`AnalysisRevision` como âncora
de procedência), BE-22 (stats por população), FE-36 (consumidora).
**Decisão 2026-09:** a figura é um artefato **persistido e auditável** —
spec + cache de dados + revisão de origem — porque o requisito de
produto é "exportar e confiar nos dados". Grupos de réplicas vivem no
spec da figura (decisão por figura); subsamples servem só de preset na
UI, nunca de binding no modelo.

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
result_cache    JSONField null — tabela de stats congelada na geração
result_revision FK AnalysisRevision null — âncora de procedência
created_by      FK User null | created_at | updated_at | active
```

- `active` = soft delete (regra da casa — a API nunca deleta).
- `result_revision` referencia a revisão head do experimento (na branch,
  quando setada) **no momento do cômputo** — é o carimbo de procedência,
  não um vínculo que bloqueia a análise.

### 2. Endpoints (app `analytics`, padrão dos demais)

| Ação | Endpoint |
|---|---|
| Listar | `GET /analytics/experiment/<id>/figures/` → `[{id, name, chart_type, updated_at, is_stale, created_by_name}]` |
| Criar | `POST /analytics/experiment/<id>/figures/` `{name, chart_type, spec, branch_id?}` → 201 com `result_cache` computado |
| Detalhe | `GET /analytics/figures/<id>/` → `{..., spec, result_cache, result_revision, is_stale, stale_since?}` |
| Editar | `PATCH /analytics/figures/<id>/` — `name`/`spec`; mudar `spec` marca a figura para recomputar (não regrava cache mudo) |
| Arquivar | `DELETE /analytics/figures/<id>/` → `active=false` (main não se aplica) |
| Recomputar | `POST /analytics/figures/<id>/recompute/` → recalcula `result_cache`, move `result_revision` para a head atual |

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

### 4. Cômputo e procedência

- `compute_figure(figure)` (em `analytics/services/figures.py`): resolve
  as populações por caminho de nomes em cada amostra dos grupos (mesma
  regra de casamento do `derive_analysis`/goToAdjacent), puxa a stats
  (`analysis_result` do gate; stats de raiz para `file`) e monta a
  `result_cache` — linhas `{group, file_data_id, file_name, population,
  value}` + metadados `{n_por_grupo, computed_at}`.
- População não-avaliável numa amostra → linha **ausente** no cache
  (nunca zero) — mesma regra do export do front (`buildAnalysisRows`).
- `result_revision` = última `AnalysisRevision` do experimento naquela
  branch (ou `experiment` se a figura é branch-less).
- `is_stale` = existe `AnalysisRevision` mais recente que
  `result_revision` no mesmo escopo — computado na leitura, não gravado.
- Respeitar compensação: stats de gates já refletem a matriz aplicada
  (mesma fonte que alimenta a UI hoje).

### 5. O que não vira revisão

CRUD de figura **não** grava `AnalysisRevision` — a timeline é da
estratégia de análise; figura é artefato de relatório. Recompute só move
a âncora `result_revision`. (Registrado para não reabrir a discussão.)

## Arquivos a tocar

- `analytics/models.py` — `AnalysisFigure` + migration (expand-only).
- `analytics/serializers.py` — `AnalysisFigureSerializer` +
  `FigureSpecSerializer` (validação do §3).
- `analytics/services/figures.py` — `compute_figure`, resolução de
  população por caminho, `is_stale`.
- `analytics/views.py` + `analytics/urls.py` — endpoints do §2.
- `analytics/tests.py` — casos abaixo.

## Critérios de aceite

- [ ] Criar figura computa `result_cache` e carimba a revisão head
- [ ] `file_data_id` de outro experimento ou inexistente → 400
- [ ] Amostra sem a população → ausente no cache (não zero)
- [ ] Editar gates depois → `is_stale=true` até `recompute/`
- [ ] `recompute/` atualiza cache e âncora, preservando spec e id
- [ ] Figura de branch compara staleness dentro da linha
- [ ] Leitor sem permissão de edição lê a figura, não edita/recomputa
- [ ] Testes: create, validação de spec, staleness, recompute, soft
      delete, escopo de permissão

## Fora de escopo

- Testes estatísticos (ANOVA e afins) — a `result_cache` é a entrada
  natural para eles, mas ficam para PRD próprio (fase 2 do FE-36).
- Figuras cross-experimento — `spec.groups` só aceita `file_data_ids`
  do próprio experimento (identidade da população via derivação é
  pré-requisito para abrir isso).
- Export da imagem — front (Plotly); backend só fornece os dados.
- Versionamento de spec (histórico de edições da figura) — v1 guarda só
  o estado atual; a timeline da análise já cobre a mudança dos dados.
