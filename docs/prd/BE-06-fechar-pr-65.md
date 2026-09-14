# BE-06 — Resolver o PR #65 (organização no list + validação de membership)

**Repo:** pandora-backend · **Item do doc:** nenhum (dívida aberta) · **Tipo:** chore/fix · **Base:** `main`
**PR existente:** https://github.com/Laboratorio-de-Analise-de-Dados/pandora-backend/pull/65 (`devin/fix-experiment-org-permissions`, **em conflito**)
**Status:** Resolvido: o PR #65 entrou na `main` via #66.

## Situação

O PR #65 traz três coisas:

1. `organization` como objeto `{id, name}` no `ListExperimentSerializer` — **ainda falta** no `develop` (hoje é `fields = "__all__"`, devolvendo só o id).
2. Validação de membership + mensagens de erro em pt-BR no `POST /experiment/init/` — **ainda falta** no `develop` (a view atual cria o experimento sem checar se o usuário pertence à organização).
3. `unique_together = [["title", "organization"]]` + migration `0007_experimentmodel_title_org_unique` — **redundante e conflitante**: o `develop` já resolveu isso por outra via, com constraints nomeadas no modelo:

```python
# fcs_parser/models.py (develop)
constraints = [
    UniqueConstraint(fields=["title", "created_by", "organization"],
                     name="unique_title_per_user_and_org",
                     condition=Q(organization__isnull=False)),
    UniqueConstraint(fields=["title", "created_by"],
                     name="unique_title_per_user_personal",
                     condition=Q(organization__isnull=True)),
]
```

O conflito do PR é exatamente esse: duas migrations `0007_*` mexendo na unicidade do mesmo modelo.

## Escopo

Reaproveitar só os itens 1 e 2, descartando o 3. Duas opções — escolher uma:

- **A (recomendada):** fechar o #65 e abrir um MR novo a partir do `develop` atual com apenas o serializer + a validação de membership. Sem migration nenhuma.
- **B:** rebasear `devin/fix-experiment-org-permissions` em `develop`, apagar a migration `0007_experimentmodel_title_org_unique.py` e reverter a mudança de `Meta` no `models.py`.

### Detalhes

- `ListExperimentSerializer`: trocar `fields = "__all__"` por lista explícita e adicionar `organization = OrganizationListSerializer(read_only=True)`. **Atenção:** isso muda o contrato consumido pelo front (`Experiment.organization` deixa de ser number) — precisa de ajuste de tipo em `pandora-front/src/types/ExperimentTypes.ts` e nos filtros por laboratório. Coordenar com um MR de front no mesmo momento, ou expor `organization_detail` em paralelo e migrar o front depois.
- `ExperimentInitView.post`: validar `title`/`type` não vazios, `organizationId` numérico e existente, e membership ativo (ou super admin) → 403 quando não for membro. Mensagens em pt-BR com chave `detail`. A checagem de título duplicado pode ser só o `try/except IntegrityError` → 400, já que as constraints do modelo cuidam do resto.

## Critérios de aceite

- [ ] Sem migration nova no MR.
- [ ] `GET /experiment/` traz `organization` no formato acordado com o front, e o filtro por laboratório volta a funcionar (card aparecendo em "Todos" e no grupo correto).
- [ ] `POST /experiment/init/` com `organizationId` de um lab do qual o usuário não é membro → 403.
- [ ] Título duplicado no mesmo escopo → 400 em pt-BR, não 500.
- [ ] PR #65 fechado (opção A) ou sem conflito (opção B).
