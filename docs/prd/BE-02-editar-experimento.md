# BE-02 — Editar informações do experimento

**Repo:** pandora-backend · **Item do doc:** 13 · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/update-experiment`
**Status:** Entregue no PR #68. Validações no serializer ([ADR-0009](../adr/0009-validacao-em-serializers.md)).

## Problema

`RetrieveDeleteExperimentView` é `generics.RetrieveDestroyAPIView` — só GET e DELETE. Não há como corrigir título, tipo ou os `values` de um experimento depois de criado.

## Escopo

Habilitar `PATCH /experiment/<experiment_id>/` para campos editáveis, com validação de unicidade coerente com as constraints já existentes no modelo.

### Contrato

```
PATCH /experiment/<experiment_id>/
Auth: IsAuthenticated
Body (todos opcionais): { "title": str, "type": str, "values": [str] }

200 → ListExperimentSerializer do experimento atualizado
400 { "detail": "Título já criado para esse laboratório." }
400 { "detail": "Título é obrigatório." }
403 { "detail": "Você não tem permissão para editar este experimento." }
```

### Regras

- Campos editáveis: **apenas** `title`, `type`, `values`. `status`, `file_status`, `total_chunks`, `received_chunks`, `organization`, `created_by`, `zip_path` seguem read-only (mudar organização depois do upload quebraria o escopo de permissão e o caminho dos arquivos).
- `title`: mesmo tratamento do `ExperimentInitView` — `strip()` + `replace(" ", "_")`, rejeitar vazio.
- Unicidade: o modelo já tem `unique_title_per_user_and_org` e `unique_title_per_user_personal`. Capturar `IntegrityError` e devolver 400 com mensagem em pt-BR em vez de 500.
- Autorização de escrita: `created_by == request.user`, admin/owner ativo da organização do experimento, ou super admin. Só leitura para os outros membros. Usar o helper criado em BE-01 se ele já estiver mergeado; senão criar aqui e o outro MR reaproveita.
- Criar um `UpdateExperimentSerializer` dedicado com `fields = ["title", "type", "values"]` — não usar `ListExperimentSerializer` (que é `fields = "__all__"`) para escrita.

## Arquivos a tocar

- `fcs_parser/views.py` — `RetrieveDeleteExperimentView` → `RetrieveUpdateDestroyAPIView`; `get_serializer_class` devolvendo o serializer de escrita em PATCH/PUT; bloquear PUT (`http_method_names`) para não exigir payload completo.
- `fcs_parser/serializers.py` — `UpdateExperimentSerializer`.
- `fcs_parser/tests/`.

## Critérios de aceite

- [ ] PATCH de título válido → 200 e valor persistido normalizado (espaços → `_`).
- [ ] PATCH com título já usado pelo mesmo usuário no mesmo escopo → 400 com mensagem em pt-BR (não 500).
- [ ] PATCH tentando alterar `status`/`organization` → campo ignorado, sem erro 500.
- [ ] Membro sem permissão de escrita → 403; GET continua funcionando pra quem tem leitura.
- [ ] `PUT` retorna 405.
