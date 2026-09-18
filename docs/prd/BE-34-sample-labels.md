# BE-34 — `SampleLabel`: labels semânticas por amostra

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/sample-labels`
**Status:** não iniciado.
**Relacionado:** BE-26 (identificação de controles — vira consumidor),
BE-25/28 (mesmo padrão de vocabulário controlado), FE-34/35 (UI de
atribuição) e FE-37 (templates — consome labels de controle na revisão).

## Problema

O sistema precisa distinguir papéis de amostra — controle negativo,
FMO, single-stain, referência biológica — e o usuário precisa marcar
isso **quando souber**, fora do fluxo de upload. Hoje o único mecanismo
é `SubsampleModel.control_type` (BE-22/ADR-0019): enum fechado, no
nível do **grupo**, pensado só para compensação. Não cobre:

- granularidade por arquivo ("só um arquivo do grupo é o controle")
- tipos de controle além da compensação (FMO, isotípico, referência)
- anotações organizacionais do usuário ("lote 3", "repetir", "dúvida")
  que o sistema não precisa entender mas a UI quer exibir como chips

Criar um enum por papel repete o erro do texto livre/enum fechado já
resolvido no BE-25: vocabulário em tabela, extensível sem deploy.

## Decisão

**Uma mecânica só de label**, com categorias. Labels de sistema
(`system_key` estável, seeded por migration, não editáveis/removíveis)
carregam a semântica que o código consome; labels de usuário são
vocabulário extensível por escopo (organização ou pessoal).

O PRD entrega **só a mecânica** — modelo, vocabulário, API e
serialização — sem nenhum consumidor grande. Isso é deliberado: entra
desacoplado e cada frente pluga depois (BE-26 marcação de controles,
FE-34/35 chips na árvore/mapa de placa, FE-37 revisão assistida por
controle, Juvia). O `control_type`/`control_channel` do subsample
**não migra** neste PRD — a compensação continua lendo o campo atual;
a convergência é decisão do BE-26 quando ele rodar.

## Escopo

### 1. Modelo

```python
class SampleLabelModel:
    name            # display (único por escopo)
    system_key      # nullable, único global — ex.: "fmo", "unstained";
                    # null em labels de usuário. O código referencia a
                    # chave, nunca o nome.
    category        # "control" | "general" — agrupa semântica;
                    # categoria "control" tem regra de exclusividade
    color           # hex — chip na UI
    scope           # "system" | "organization" | "personal"
    organization    # FK nullable — preenchido quando scope=organization
    created_by      # FK User
    active          # soft delete (A API nunca deleta)

class FileLabelModel:  # through explícito
    file_data  → FileDataModel
    label      → SampleLabelModel
    created_by, created_at
```

- Seeds via migration: `unstained`, `fmo`, `isotype`, `single_stain`,
  `biological_ref`, `beads` — `category="control"`, `scope="system"`,
  nomes display em PT-BR.
- **Invariante de exclusividade**: no máximo **uma** label de
  `category="control"` por `file_data`. Não cabe em constraint SQL
  (a categoria mora na label) → validação no serializer + função de
  serviço única (`set_file_labels`) como único caminho de escrita.
- Labels de `scope="system"` não podem ser criadas/editadas/inativadas
  pela API — só por migration.
- Label de usuário inativada vira soft delete: vínculos ficam, a label
  sai do vocabulário e da serialização.

### 2. API

- `GET /labels/` — vocabulário visível ao usuário (system + sua
  organização + pessoais). `?category=` para filtrar.
- `POST /labels/` — cria label de usuário (`scope` deduzido: com org →
  organization, sem org → personal). `system_key`/`category="control"`
  rejeitados para escopo não-system.
- `PATCH /labels/<id>/` — renomear/recolorir label própria.
- `PUT /file-data/<id>/labels/` — define o conjunto de labels da
  amostra (payload = ids). Exclusividade de controle violada → 400.
  Permissão: `can_edit_experiment` da amostra.
- Serialização: `labels: [{id, name, color, system_key, category}]` na
  listagem de amostras (join barato — prefetch obrigatório, sem N+1).

### 3. Sugestão assistida (herdada do BE-26)

- A heurística de filename (`unstained`, `fmo`, `neg`, `control`,
  `comp`, `beads`) expõe `suggested_labels` na listagem — informativo,
  nunca aplicado. Função pura em `fcs_parser/services/`, testável.

## Arquivos a tocar

- `fcs_parser/models.py` + migration — `SampleLabelModel`,
  `FileLabelModel`, seeds de sistema
- `fcs_parser/serializers.py` — vocabulário, `PUT` de labels,
  `labels`/`suggested_labels` na listagem (ADR-0009)
- `fcs_parser/views.py` + `urls.py` — endpoints
- `fcs_parser/permissions.py` — edição via `can_edit_experiment`;
  vocabulário de usuário: dono/org
- `fcs_parser/services/` — `set_file_labels` (único escritor),
  heurística de sugestão

## Critérios de aceite

- [ ] Labels de sistema seeded; API recusa criar/editar/inativar
      `scope="system"`
- [ ] `PUT` define conjunto de labels; segunda label de controle → 400
- [ ] Label de usuário criada por usuário sem org → `scope=personal`,
      não vaza para outras contas
- [ ] Listagem expõe `labels` + `suggested_labels` sem N+1
- [ ] Nenhum comportamento existente muda: `control_type` do subsample
      segue intacto e consumido só pela compensação
- [ ] `python manage.py test` verde; `makemigrations --check` limpo

## Fora de escopo

- **Consumidores**: UI de chips (FE-34/35), marcação assistida em lote,
  uso pelo Juvia, uso pelo FE-37 — cada um pluga depois na mecânica
- **Migração do `control_type` do subsample** para labels — decisão
  do BE-26 quando ele rodar (avaliar se compensação passa a ler label
  ou se o campo permanece só para comp)
- Propagação de label por `content_guid` entre experimentos — ideia
  registrada; abre quando um consumidor pedir
- Label no nível de subsample/grupo — o alvo deste PRD é `file_data`;
  label de grupo entra se um caso real pedir
- Vocabulário global compartilhado entre organizações (labels de
  usuário ficam por escopo; promover a "system" é via migration)
