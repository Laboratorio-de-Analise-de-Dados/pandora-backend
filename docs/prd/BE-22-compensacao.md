# BE-22 — Compensação (spillover): aceitar matriz do FCS ou calcular de controles

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/compensacao`
**Status:** parcial em `feat/analysis-checkpoints` — entregue a
**sinalização** (`compensated` na listagem via headers `$SPILLOVER`/`$COMP`)
e `GET /experiment/<id>/compensations/embedded` (leitura da matriz embutida).
Pendente: `CompensationMatrix`, controles/subsamples, compute e apply
(escopo 1, 3, 4, 5 e 6 abaixo).
**ADRs:** [0018](../adr/0018-compensacao-matriz-versionada-por-experimento.md)
(matriz versionada por experimento, aplicada na leitura) ·
[0019](../adr/0019-controles-de-compensacao-como-subsamples.md)
(controles como subsamples marcados).

## Problema

Canais fluorescentes vazam sinal uns nos outros (spillover espectral).
Hoje o Pandora lê os eventos crus e nunca compensa — gates e estatísticas
em canais de fluorescência ficam enviesados em experimentos multicolor.

O arquivo .fcs pode trazer a matriz embutida (`$SPILLOVER` no FCS 3.x,
`$COMP` legado), que já está nos `headers` crus que o parse grava em
`FileDataModel.headers`. Mas ela pode faltar ou estar errada — o fluxo
padrão é recalcular a partir de controles single-stain + negativo.

## Escopo

### 1. `CompensationMatrix` — entidade por experimento

Novo modelo (ADR-0018): `experiment` FK, `name`, `matrix` (JSON N×N,
spillover — fração do fluorócromo j detectada no canal i),
`channels` (lista ordenada que dá os eixos da matriz), `source`
(`"fcs_header" | "computed" | "manual"`), `created_by`, `created_at`,
`active` (soft delete). Matrizes convivem; **no máximo uma ativa** por
experimento (`active_compensation` no ExperimentModel ou flag na matriz —
detalhe de implementação, invariante no banco).

### 2. Aceitar a compensação embutida

- `GET /experiment/<id>/compensations/embedded` — varre os `headers` das
  amostras ativas atrás de `$SPILLOVER`/`$COMP` e devolve a matriz
  encontrada (canais + valores), ou 204 se nenhuma amostra a tem.
- `POST /experiment/<id>/compensations/from-header` — materializa a matriz
  embutida como `CompensationMatrix(source="fcs_header")`. 409 se a
  amostra não tiver a keyword.
- Parse da keyword: `$SPILLOVER` é `n,ch1,...,chn,v11..vnn` (valores em
  row-major); validar que `n` bate com a quantidade de canais listados.

### 3. Controles via subsamples (ADR-0019)

`SubsampleModel` ganha `control_type` (`null | "unstained" | "single_stain"`)
e `control_channel` (obrigatório quando single_stain). Editável pelo
endpoint de subsample existente; validar:

- `control_channel` ∈ canais fluorescentes do experimento
  (`experiment.values` menos FSC/SSC/Time);
- um single_stain por canal por experimento (conflito → 400 nomeando o
  subsample que já cobre o canal);
- um unstained por experimento na v1.

### 4. Calcular nova compensação

`POST /experiment/<id>/compensations/compute`:

- Descobre o mapeamento canal→controle pelos subsamples marcados;
  payload pode trazer override explícito (`{"channel": "FITC-A",
  "file_data_id": N}`) para apontar amostras fora de subsample-controle.
- Exige: unstained + ao menos 2 single_stains (matriz 1×1 não compensa
  nada — erro 400 explicando).
- Algoritmo (MFI por mediana, robusto a outlier): para cada canal j com
  controle single-stain e cada canal fluorescente i:

  `S[i][j] = (MFI_i(controle_j) − MFI_i(negativo)) / (MFI_j(controle_j) − MFI_j(negativo))`

  `MFI` = mediana dos eventos do controle naquele canal, fazendo pool das
  réplicas do subsample. Denominador ~0 → 400 nomeando o canal.
- Persiste `CompensationMatrix(source="computed")` com os canais
  fluorescentes cobertos pelos controles.

### 5. Aplicar / remover

- `POST /experiment/<id>/compensations/<matrix_id>/apply` — torna a matriz
  a ativa do experimento (`can_edit_experiment`).
- `POST /experiment/<id>/compensations/remove` — limpa a ativa.
- Os dois gravam **AnalysisRevision** (ação nova, ex.: `compensation_apply`
  / `compensation_remove`) — a mudança altera resultados de gates e entra
  na timeline/revert do BE-08/BE-20.
- Listagem/edição: `GET /experiment/<id>/compensations/`,
  `PATCH/DELETE /analytics/compensations/<id>/` (renomear; delete é
  `active=false`).

### 6. Aplicação na leitura (ADR-0018)

Quando o experimento tem matriz ativa, `compute_density`, scatter,
histograma e estatísticas de gate multiplicam os eventos dos canais
cobertos por `S⁻¹` **antes** do binning/escala. A chave de cache de
densidade incorpora a identidade da matriz ativa — sem isso o
FileBasedCache serviria dado compensado como cru.

## Arquivos a tocar

- `fcs_parser/models.py` — `SubsampleModel.control_type/control_channel`;
  `CompensationMatrix` (novo, provavelmente em `analytics/` ou app
  próprio se crescer); flag/fk de matriz ativa no `ExperimentModel`.
- `fcs_parser/services/` — `compensation.py` novo: parse de
  `$SPILLOVER`/`$COMP`, cálculo da matriz, aplicação `S⁻¹` no DataFrame.
- `utils/density.py` — parâmetro de compensação no pipeline de leitura +
  chave de cache.
- `fcs_parser/views.py` + `urls.py` (ou `analytics/`) — endpoints do
  escopo 2, 4 e 5.
- `fcs_parser/serializers.py` — validações do escopo 3 e payloads novos.
- Migration nova; testes de cálculo (matriz conhecida → coeficientes
  esperados), validações e permissões.

## Critérios de aceite

- [ ] `GET .../compensations/embedded` devolve a matriz `$SPILLOVER` de um
      FCS que a tem; 204 quando não tem.
- [ ] `from-header` cria `CompensationMatrix(source="fcs_header")`
      idêntica à embutida.
- [ ] `compute` com controles marcados gera matriz cujos coeficientes
      batem com dataset sintético de spillover conhecido.
- [ ] 400 nomeando o conflito: canal sem controle, canal com dois
      single_stains, `control_channel` não fluorescente, denominador ~0.
- [ ] Apply/remove viram revisões na timeline e são revertíveis.
- [ ] Densidade/estats mudam ao aplicar e voltam ao remover (cache
      invalidado corretamente).
- [ ] Escopo `experiments_visible_to` na leitura, `can_edit_experiment`
      na escrita; delete é soft.

## Fora de escopo

- UI de compensação (PRD de front próprio — editor de matriz, marcação
  de controles, toggle de aplicada).
- Spectral unmixing (full-spectrum) — modelo de referência diferente.
- Múltiplos unstained por autofluorescência de tecido.
- Edição manual célula-a-célula da matriz na v1 (`source="manual"` existe
  no modelo para criação de matriz por upload/edição futura).
- FMO controls (gate strategy, não spillover).
