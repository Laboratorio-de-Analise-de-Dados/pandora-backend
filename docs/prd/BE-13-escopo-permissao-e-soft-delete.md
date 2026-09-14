# BE-13 — Escopo de permissão em todo lookup + soft delete de experimento

**Repo:** pandora-backend · **Tipo:** hardening/refactor · **Base:** `main`
**Branch sugerida:** `feat/scoped-lookups`
**Decisão em:** [ADR-0014](../adr/0014-todo-lookup-via-queryset-escopada.md);
fecha a brecha do [ADR-0005](../adr/0005-api-nunca-deleta.md) e aplica o
[ADR-0009](../adr/0009-validacao-em-serializers.md) no fluxo de upload.

## Problema

Endpoints antigos não passavam pelo escopo `experiments_visible_to`:

- leitura de amostra (`list/data`, `file/<id>/list`, `density`, `stats`,
  `headers`) buscava `FileDataModel` por id sem checar dono → **IDOR**;
- upload legado (`upload-chunk/`, `complete/`) usava `objects.get()` sem
  permissão → **IDOR de escrita** + 500 em vez de 404;
- `analytics/` tinha views sem `permission_classes` → como o default DRF é
  `AllowAny`, CRUD de gate era possível **sem autenticação**;
- `DELETE /experiment/<id>/` fazia hard delete em cascata (anterior ao
  ADR-0005);
- `check-hash/` global devolvia `file_name` de blobs de outras organizações.

## Escopo

### Permissão (o contrato)

- Todo lookup por id usa queryset escopado: `experiments_visible_to`,
  `file_data_visible_to`, `uploads_visible_to` (`fcs_parser/permissions.py`),
  `gates_visible_to` (`analytics/permissions.py`, novo).
- Invisível → 404; visível sem direito de escrita → 403
  (`require_can_edit_*` levanta `PermissionDenied`).
- Toda view declara `permission_classes`; público é `[]` explícito.
- `check-hash/` passa a responder só sobre experimentos visíveis ao usuário.

### Soft delete de experimento (ADR-0005)

```
DELETE /experiment/<id>/
204 — registro permanece com active=false
403 — membro do lab sem papel de admin/dono
404 — experimento fora do escopo (ou já inativo)
```

- `GET /experiment/` esconde inativos; `?include_inactive=true` os mostra.
- Amostras/gates de experimento inativo ficam invisíveis (o escopo resolve
  via `experiment__in`).

### Validação em serializer (ADR-0009)

Novos serializers de entrada: `ExperimentInitSerializer`,
`ExperimentFileInitSerializer`, `ChunkUploadSerializer`,
`ExperimentCompleteSerializer`. O `@extend_schema` passa a apontar para o
serializer real (schema = validação). Erros seguem `{"detail": "..."}` via
`_first_error`.

### Limpeza de resíduos

- Removidos `citosharp/citosharp/`, `citosharp/manage.py` (restos de um
  `startproject` rodado dentro do pacote), `fcs_parser/utils/`
  (`__initi__.py` — typo, versão quebrada de `can_edit_experiment`),
  `accounts/utils/` e `analytics/utils/` (mixins duplicados mortos).
- `utils/` ganha `__init__.py` (pacote regular).
- `app_name` corrigido para `fcs_parser`.

### Infra/docs

- `docker-compose.yml` (dev): `postgres:16` pinado, credenciais via env com
  defaults explícitos de dev, `makemigrations` sai da subida — gerar com
  `docker compose exec web python manage.py makemigrations` e commitar.
- `README.md` e `AGENTS.md` sincronizados (sem Celery/Redis).

## Critérios de aceite

- [ ] Usuário fora do experimento recebe 404 em leitura/escrita de amostra,
  upload, gate, download e detalhe — e 401 quando anônimo.
- [ ] `DELETE /experiment/<id>/` inativa: some da listagem, volta com
  `include_inactive=true`, linha continua no banco.
- [ ] Membro de lab sem papel admin/dono recebe 403 no DELETE.
- [ ] Fluxo upload (init → chunks → complete) intacto para o dono.
- [ ] `TEST=1 python manage.py test` verde.
