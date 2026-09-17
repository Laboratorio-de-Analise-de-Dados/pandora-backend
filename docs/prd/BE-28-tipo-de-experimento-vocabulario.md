# BE-28 — Tipo de experimento como vocabulário controlado criável por usuário

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Status:** implementado em `feat/experiment-type` · ADR:
[ADR-0022 — vocabulário de tipos](../adr/0022-experiment-type-vocabulario-controlado.md)
(branch `docs/adr-0022-experiment-type`) · Front: autocomplete+criar nos
dialogs de experimento (`refactor/tema-pandora-dark`).

## Problema

`ExperimentModel.type` é um `CharField` livre. Sem vocabulário, cada
experimento pode grafar o tipo de um jeito ("stem cell", "Stem Cell", "SC")
e estatística/filtro por tipo vira bagunça. Mas um enum fechado também não
serve — tipos de experimento variam por laboratório e criar um tipo novo não
pode exigir deploy nem admin.

## Decisão (ADR-0022)

Vocabulário **controlado mas extensível**: tabela `experiment_type` com
dedup normalizado. Quem pode introduzir um tipo novo é **admin ou o dono
do experimento** — quem cria o próprio experimento é dono por definição,
então a criação implícita via save() cobre o fluxo normal; membro
editando experimento alheio só escolhe entre tipos existentes (PATCH com
tipo inédito → 400). A UX (autocomplete que sugere primeiro, "Criar X"
como último recurso) faz a pressão de reúso — a API garante a
convergência.

## Escopo

### `ExperimentTypeModel`

- `name` — casing canônico de exibição (o primeiro digitado vence).
- `name_normalized` — `lower()` + whitespace colapsado, `unique=True` —
  é o que impede "Stem Cell" × "stem cell" de virarem dois registros.
- `active` (soft delete para curadoria futura), `created_by`, `created_at`.
- `resolve(name, user)` — get-or-create normalizado; `ExperimentModel.save()`
  chama isso em **todo** caminho de escrita (API, services, shell, admin),
  então o vocabulário converge sem depender de endpoint específico.

### Compatibilidade com `type` string

`type` continua CharField e continua sendo o contrato da API (payload e
resposta). `experiment_type` é a FK canônica paralela. `save()` sincroniza
os dois: resolve o vocabulário, preenche a FK e regrava `type` com o casing
canônico. `type` vazio/None limpa a FK.

### `GET|POST /experiment/types/`

- `GET` — tipos ativos ordenados por nome (case-insensitive) para o
  autocomplete; qualquer autenticado lê.
- `POST {"name": "X"}` — **admin-only**: sem experimento no contexto, só
  admin introduz entradas soltas (403 para demais). **Idempotente**: nome
  já existente (após normalização) devolve a entrada canônica com `200`,
  não erro; tipo novo devolve `201`. O vocabulário é global, não por
  organização.
- Permissão centralizada em `can_create_experiment_type(user, experiment)`
  (`fcs_parser/permissions.py`): super admin sempre; com experimento, só
  o `created_by`.

### Migration `0018`

Schema (`ExperimentTypeModel` + FK) **mais** data migration no mesmo
arquivo: varre experimentos com `type` legado, dedup por normalização
(primeiro casing por id vence), popula a FK e regrava `type` canônico.

## Arquivos tocados

- `fcs_parser/models.py` — `ExperimentTypeModel`, FK + sync no `save()`
- `fcs_parser/migrations/0018_*.py` — schema + backfill
- `fcs_parser/serializers.py` — `ExperimentTypeSerializer`
- `fcs_parser/views.py` — `ExperimentTypeListCreateView`
- `fcs_parser/urls.py` — `types/` antes do catch-all `<str:experiment_id>/`
- `fcs_parser/tests.py` — `ExperimentTypeTestCase` (8 testes)

## Critérios de aceite

- [x] `POST /experiment/types/` cria tipo novo (201) e deduplica
      case/whitespace-insensitive devolvendo o canônico (200) — admin only
- [x] `GET /experiment/types/` lista ativos ordenados; exige autenticação
- [x] Experimento criado com tipo inédito popula o vocabulário
      automaticamente (o criador é o dono)
- [x] Variação de casing no `type` reusa a entrada e normaliza o label
- [x] PATCH de `type` por dono/admin resolve no vocabulário; membro em
      experimento alheio com tipo inédito leva 400; `type` vazio limpa a FK
- [x] Backfill deduplica tipos legados e preserva casing canônico
- [x] Suíte completa verde (210 testes)

## Fora de escopo

- Curadoria administrativa (mesclar/renomear tipos, inativar) — o campo
  `active` e o `name` canônico já suportam; endpoint/admin fica para quando
  a fragmentação real aparecer.
- Tipo por organização — o vocabulário é global (decisão do ADR).
- Sugestão rankeada por frequência de uso — a listagem é alfabética.
