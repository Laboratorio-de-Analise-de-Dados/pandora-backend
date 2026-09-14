# BE-01 — Desativar (soft delete) um arquivo do experimento

**Repo:** pandora-backend · **Item do doc:** 6 · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/disable-file-data`
**Status:** Entregue no PR #67. Decisão em [ADR-0001](../adr/0001-soft-delete-de-amostras.md); retenção/limpeza segue fora de escopo ([ADR-0004](../adr/0004-zip-como-fonte-de-verdade-parquet-como-cache.md)).

## Problema

Hoje só é possível apagar o experimento inteiro (`DELETE /experiment/<experiment_id>/` via `RetrieveDeleteExperimentView`). Não existe rota para remover uma amostra específica, então um arquivo carregado por engano obriga a refazer o experimento.

## Decisão de arquitetura: soft delete, não exclusão física

Neste primeiro momento **nada é apagado do disco nem do banco**. O arquivo é *desativado* (freezer):

- protege contra exclusão acidental de dados de pesquisa;
- permite reativar;
- abre caminho para a política de retenção (limpeza automática de arquivos inativos e sem acesso há X tempo) numa **rotina futura**, fora do escopo deste MR.

Consequência a registrar no PR: sem a rotina de limpeza, o volume em disco cresce indefinidamente. O MR deve deixar isso explícito (nota no PR + comentário no modelo) para não virar dívida esquecida.

## Escopo

`POST /experiment/file/<int:file_id>/disable` e `POST /experiment/file/<int:file_id>/enable`, alterando um flag no `FileDataModel`.

### Contrato

```
POST /experiment/file/<file_id>/disable
Auth: IsAuthenticated
200 { "id": 10, "file_name": "amostra1.fcs", "active": false, "deactivated_at": "2026-08-27T22:00:00Z" }

POST /experiment/file/<file_id>/enable
200 { "id": 10, "file_name": "amostra1.fcs", "active": true, "deactivated_at": null }

403 { "detail": "Você não tem permissão para alterar este experimento." }
404 { "detail": "Not found." }
```

Listagem passa a aceitar filtro:

```
GET /experiment/list/data/<experiment_id>/?include_inactive=true
→ default (sem o param): só arquivos ativos
```

### Modelo

`FileDataModel` ganha:

```python
active = models.BooleanField(default=True, db_index=True)
deactivated_at = models.DateTimeField(null=True, blank=True)
deactivated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="deactivated_files")
```

Migration simples (nenhum backfill necessário: `default=True`).

### Regras

- **Gates da amostra desativada não são apagados.** Ficam inacessíveis pela listagem default e voltam intactos ao reativar. Documentar isso na mensagem de confirmação do front (FE-04).
- Endpoints/queries que consomem `FileDataModel` devem respeitar o flag: `GetExperimentFiles` (default só ativos), e as views de densidade/stats/recompute devem responder 409/400 com `detail` claro (ex.: `Arquivo desativado.`) em vez de servir dados de arquivo inativo.
- Autorização: mesma regra de `RetrieveDeleteExperimentView.get_queryset` (super admin, membro ativo da organização, ou `created_by`). Extrair para `fcs_parser/permissions.py::experiments_visible_to(user)` e reutilizar — BE-02 usa o mesmo helper.
- Cache de densidade: invalidar (`utils.density.invalidate_density`) ao desativar, para não servir heatmap de arquivo inativo; reativar recalcula sob demanda.
- **Não** apagar `parquet_path`, `fcs_path` nem mexer no `zip_path` do experimento.
- Contagem/relatórios do experimento devem ignorar arquivos inativos.

## Fora de escopo (registrar como issues, não codar agora)

1. **Rotina de retenção**: comando/management task que limpa parquet (e depois o registro) de arquivos inativos sem acesso há X tempo, usando o `last_accessed` que já existe no modelo. Definir X e se apaga só o cache parquet (regenerável do ZIP) ou o registro inteiro.
2. **Reupload do mesmo arquivo**: ao subir um arquivo com nome já existente no experimento, perguntar ao usuário `sobrescrever` ou `reativar o existente`. Precisa de uma chave de identidade do arquivo (nome + hash do conteúdo?) e mudança no fluxo de upload chunked. Ainda a amadurecer.

## Arquivos a tocar

- `fcs_parser/models.py` + migration
- `fcs_parser/views.py` — `DisableFileDataView` / `EnableFileDataView`; filtro em `GetExperimentFiles`; guarda nas views de densidade/stats/recompute
- `fcs_parser/urls.py` — rotas `file/<int:file_id>/disable` e `.../enable` (antes da rota coringa `<str:experiment_id>/`, seguindo o padrão das rotas `file/...` existentes)
- `fcs_parser/permissions.py` (novo)
- `fcs_parser/serializers.py` — expor `active` no `ListFileDataSerializer`
- `fcs_parser/tests/`

## Critérios de aceite

- [ ] Dono desativa um arquivo → 200, `active=False`, `deactivated_at` preenchido; parquet e ZIP intactos no disco.
- [ ] `GET /experiment/list/data/<id>/` deixa de listar o arquivo; com `?include_inactive=true` ele aparece com `active: false`.
- [ ] Reativar → volta a aparecer no default, com os gates intactos.
- [ ] Densidade/stats de arquivo desativado → erro com `detail` claro, não 500.
- [ ] Usuário sem vínculo com o experimento → 403/404, nada alterado.
- [ ] Endpoints em `/api/schema/`.
- [ ] PR descreve a política de retenção pendente e linka as duas issues de fora de escopo.
