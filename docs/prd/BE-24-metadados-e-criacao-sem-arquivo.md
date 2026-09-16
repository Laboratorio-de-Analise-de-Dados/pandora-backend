# BE-24 — Metadados do experimento: criação sem arquivo, `description` e `values` read-only

**Repo:** pandora-backend · **Tipo:** feature + tightening · **Base:** `main`
**Status:** implementado em `feat/analysis-checkpoints` · Front: mesma entrega
em `pandora-front` (dialog de criação/edição).

## Problema

Três arestas na superfície "metadados do experimento":

1. **Criar experimento exigia upload.** O único caminho de criação era
   `POST /experiment/init/` — que cria a linha já em `status="uploading"`
   esperando chunks. Mas o modelo foi desenhado para experimento sem dados
   (`status="new"` e `file_status="pending"` são os defaults) e o upload
   tardio já existe (BE-12: `files/init → upload-chunk → files/complete`).
   A exigência era acoplamento de implementação, não regra de negócio.
2. **`description` não existia.** Não havia campo livre para contexto do
   experimento (objetivo, painel, notas de lote).
3. **`values` era gravável via PATCH.** Os canais são derivados dos FCS —
   `process_experiment_zip`/`process_experiment_fcs` sobrescrevem
   `experiment.values` a cada extração. Edição manual produzia dessincronia
   que se perdia no próximo upload.

## Escopo

### `POST /experiment/` — criação sem arquivo

```
POST /experiment/
{ "title": "...", "type": "...", "description": "...", "organizationId": 7 }

201 ListExperimentSerializer do experimento criado
400 título duplicado no mesmo contexto / tipo vazio / payload inválido
403 usuário sem membership ativa na organização de destino
```

- Nasce com os defaults do modelo: `status="new"`, `file_status="pending"`,
  sem amostras — `preview_available` já anota `false` e `my_role` segue a
  regra normal (owner pessoal / papel da org).
- Amostras entram depois pelo fluxo BE-12; a extração já leva o experimento
  a `status="done"` sozinha (nenhuma transição extra necessária).
- `ExperimentListView` virou `ListCreateAPIView`: mesmo path, GET lista e
  POST cria — espelha `SubsampleListCreateView`.
- A validação de contexto (unicidade de título por contexto + membership na
  org) foi extraída para `validate_experiment_context()` e é compartilhada
  com `ExperimentInitSerializer` — as duas portas de criação aplicam a
  mesma regra.

### `description` — campo livre opcional

- `ExperimentModel.description = TextField(blank=True, default="")` —
  migration `0017_experimentmodel_description`.
- Gravável na criação (ambos os caminhos: `init/` e `POST /experiment/`) e
  via `PATCH /experiment/<id>/`.
- `ListExperimentSerializer` usa `fields = "__all__"` — o campo desce na
  listagem e no retrieve sem tocar o serializer.

### `values` sai da escrita

- `UpdateExperimentSerializer.fields` passa de `["title", "type", "values"]`
  para `["title", "type", "description"]`.
- PATCH com `values` no corpo passa a ser ignorado silenciosamente (DRF
  ignora campos não declarados) — o canal continua visível em leitura e é
  sempre recomputado pela extração.

## Arquivos tocados

- `fcs_parser/models.py` — campo `description`
- `fcs_parser/migrations/0017_experimentmodel_description.py`
- `fcs_parser/serializers.py` — `validate_experiment_context`,
  `ExperimentCreateSerializer`, `description` no `ExperimentInitSerializer`,
  `values` fora do `UpdateExperimentSerializer`
- `fcs_parser/views.py` — `ExperimentListView` vira `ListCreateAPIView` com
  `create` explícito (resposta `ListExperimentSerializer`, 201)
- `fcs_parser/tests.py` — `ExperimentCreateEmptyTestCase` (6 testes)

## Critérios de aceite

- [x] `POST /experiment/` sem arquivo cria experimento `new`/`pending`
      visível na listagem
- [x] `description` grava na criação (init e create) e no PATCH
- [x] Unicidade de título por contexto e membership aplicadas igual ao init
- [x] PATCH `values` não altera os canais
- [x] Suíte completa verde (202 testes)

## Fora de escopo

- Exibir `description` na listagem/workspace além do dialog — decisão de
  apresentação no front.
- Backfill de descrição para experimentos existentes — campo nasce vazio,
  sem necessidade de migração de dados.
- Slug/amigável ou templates de experimento — BE-19 trata templates.
