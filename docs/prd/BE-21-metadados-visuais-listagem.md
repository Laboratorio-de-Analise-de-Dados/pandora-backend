# BE-21 — Metadados visuais da listagem de experimentos (preview, papel, progresso)

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/experiment-list-visual-meta`
**Status:** implementado em `feat/analysis-checkpoints`.
**Dependência de:** [FE-26](../../../pandora-front/docs/prd/FE-26-tema-visual-pandora.md)
(refactor visual — os mockups dos cards de experimento assumem thumbnail do
plot, chip de papel do usuário e progresso percentual).

## Problema

O refactor visual do front (FE-26) desenha os cards de experimento com três
informações que a API hoje **não expõe**:

1. **Miniatura do plot** (heatmap FSC-A × SSC-A) no card — o mockup mostra um
   preview da densidade da primeira amostra.
2. **Chip de papel do usuário** ("Dono" / "Editor" / "Viewer") — o card mostra
   `created_by_name`, mas não diz o papel **do usuário logado** naquele
   experimento.
3. **Progresso percentual** ("Processando 45%") — hoje a listagem só expõe
   `status`/`file_status` qualitativos; o modelo já guarda
   `total_chunks`/`received_chunks` (upload), mas nada é serializado.

O front já está preparado para consumir os campos como opcionais
(`Experiment.progress`, `Experiment.my_role`, `Experiment.preview` em
`pandora-front/src/types/ExperimentTypes.ts`) — sem eles os cards renderizam
como hoje, sem thumbnail e com chip de status qualitativo.

## Escopo

### 1. `my_role` na listagem

`ListExperimentSerializer` (`fcs_parser/serializers.py:224`) ganha campo
calculado com o papel do **request.user** naquele experimento:

- Experimento pessoal (`organization=null`): `"owner"` quando
  `created_by == request.user` (a listagem já é escopada por
  `experiments_visible_to`, então pessoal listado é sempre do próprio).
- Experimento de organização: `Membership.role` do usuário na org —
  serializar o `name`/`slug` do role (ex.: `"dono"`, `"editor"`, `"viewer"`).

Cuidado com N+1: resolver via `Prefetch`/annotate no queryset de
`ExperimentListView` (`fcs_parser/views.py:260`), não query por objeto.

### 2. `progress` na listagem

Campo `progress` (int 0–100, `null` quando não aplicável):

- `status == "uploading"` → `len(received_chunks) / total_chunks * 100`
  (guardar divisão por zero / `total_chunks` nulo).
- `status == "processing"` → processamento é síncrono por request (sem
  Celery — ver AGENTS.md); **não inventar contador**. Expor `progress: null`
  e deixar o front mostrar "Processando" sem percentual, **ou** derivar de
  contagem de `FileDataModel` parseados vs. total do ZIP se essa informação
  já existir — caso contrário registrar como limitação conhecida.
- Demais status → `null`.

### 3. Preview do experimento (thumbnail)

`GET /experiment/<id>/preview/` — nova view escopada em
`experiments_visible_to`, retornando um **histograma 2D de baixa resolução**
da primeira amostra ativa do experimento:

- Reusar `compute_density`/`get_cached_density` de `utils/density` com
  `bins=48` (ou 64) e os dois primeiros canais de `experiment.values`
  (fallback FSC-A/SSC-A se existirem nos headers).
- Resposta JSON compacta: `{ "z": [[...]], "x_edges": [...], "y_edges": [...] }`
  — o front renderiza num `<canvas>` com a colorscale do tema. PNG pré-gerado
  fica como alternativa descartada por enquanto (exige storage + invalidação;
  o JSON baixa-resolução é barato e cacheável via `FileBasedCache`, ADR-0004).
- `404` quando o experimento não tem amostra ativa; `204`/payload vazio
  quando ainda está em `new`/`uploading`.
- Cache: chave derivada de `density_cache_key` + flag `preview`; o card é
  atualizado por refetch da listagem, não precisa de freshness forte.

Sem experimento com dados → front esconde a área da thumbnail (opcional no
contrato: campo `preview_available: bool` na listagem evita request inútil).

## Arquivos a tocar

- `fcs_parser/serializers.py` — `ListExperimentSerializer`: `my_role`,
  `progress`, `preview_available`.
- `fcs_parser/views.py` — `ExperimentListView` (annotate do role) +
  `ExperimentPreviewView` novo.
- `pandora/urls.py` ou `fcs_parser/urls.py` — rota do preview.
- `utils/density.py` — reuso; só tocar se faltar parâmetro de resolução.
- Testes de `my_role` (pessoal/org), `progress` durante upload, e preview
  404/200.

## Critérios de aceite

- [ ] `GET /experiment/` devolve `my_role`, `progress`, `preview_available`
      por experimento, sem N+1 (assertNumQueries ou similar).
- [ ] `GET /experiment/<id>/preview/` devolve histograma 2D pequeno de
      amostra ativa; 404 sem amostra; escopo via `experiments_visible_to`.
- [ ] `progress` reflete `received_chunks/total_chunks` durante upload.
- [ ] Sem quebra de contrato: campos novos são aditivos/opcionais.

## Fora de escopo

- Thumbnail PNG persistida (dívida futura se o JSON pesar).
- Preview de **gates** na thumbnail (mostrar geometria do gate por cima).
- Progresso granular de parsing durante `processing` (síncrono; exigiria
  contador novo no pipeline — documentar como limitação).
- Preview de workspaces/templates (BE-19).
