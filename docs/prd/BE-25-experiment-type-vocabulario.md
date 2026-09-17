# BE-25 — `experiment_type`: tipo de análise como vocabulário controlado

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/experiment-type`
**Status:** não iniciado — ADR-0023 (Proposto).

## Problema

Sem rotular o tipo de análise do experimento (stem cell, PBMC,
beads/CBA, tripanossomatídeos, ...), não há como comparar "modelo × tipo"
quando o Juvia gerar gates — e a pesquisa perde o eixo de agregação que
permite aprender qual configuração funciona para cada tipo. Texto livre
fragmenta labels; enum congela o vocabulário (ADR-0023).

## Escopo

### 1. Modelo

- `ExperimentTypeModel`: `name` (único), `description`, `active`,
  `created_by` — vocabulário extensível por admin, sem deploy
- `ExperimentModel.experiment_type` — FK nullable (experimentos antigos
  ficam sem tipo; classificação retroativa é manual)
- Opção "outro" + descrição livre como escape — tipo novo de verdade vira
  entrada na tabela, não texto solto

### 2. API

- CRUD do vocabulário restrito a admin (`/experiment-types/`)
- `experiment_type` no create/edit de experimento (serializer, ADR-0009)
- Filtro `?experiment_type=` na listagem de experimentos
- Expõe `name` na listagem (join barato, evita N+1 do front)

## Arquivos a tocar

- `fcs_parser/models.py` + migration — `ExperimentTypeModel`, FK
- `fcs_parser/serializers.py` — campo + validação
- `fcs_parser/views.py` + `urls.py` — CRUD do vocabulário, filtro
- `fcs_parser/permissions.py` — escopo admin para CRUD de vocabulário

## Critérios de aceite

- [ ] Admin cria/edita/inativa tipos sem deploy
- [ ] Experimento criado/editado com `experiment_type` válido; inválido → 400
- [ ] Listagem filtra por tipo e expõe o nome
- [ ] Migration reversível

## Fora de escopo

- Uso do tipo pelo Juvia (default de modelo por tipo) — fase posterior,
  depende de estatística acumulada (juvia ADR-0003)
- Classificação automática/retroativa em massa
