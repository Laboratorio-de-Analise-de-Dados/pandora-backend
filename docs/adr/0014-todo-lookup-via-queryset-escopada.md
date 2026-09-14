# ADR-0014 — Todo lookup por id passa por queryset escopado

- **Status:** Aceito
- **Data:** 2026-09-13
- **Contexto do código:** `fcs_parser/views.py`, `analytics/views.py`,
  `fcs_parser/permissions.py`, `analytics/permissions.py`

## Contexto

O escopo de acesso (`experiments_visible_to`) existia, mas era aplicado só
nos endpoints mais novos (copy, download). Os demais buscavam o objeto por id
direto — `FileDataModel.objects.get`, `get_object_or_404(GateModel, ...)`,
`FileModel` sem filtro de dono. Resultado observado:

- qualquer usuário autenticado lia amostras, headers, density e stats de
  qualquer experimento (IDOR);
- pior: `analytics/` tinha views **sem `permission_classes`** — como o
  settings não define `DEFAULT_PERMISSION_CLASSES`, o default do DRF é
  `AllowAny`, e criar/editar/deletar gates era possível **sem autenticação**;
- `ExperimentModel.objects.get(id=...)` inexistente devolvia 500 em vez de
  404;
- `DELETE /experiment/<id>/` fazia hard delete em cascata, anterior ao
  ADR-0005;
- `check-hash/` global revelava `file_name` de blobs de outras organizações.

## Decisão

**Nenhum handler toca o banco por id cru.** Todo lookup passa por um queryset
escopado exportado de `permissions.py` do app:

```python
get_object_or_404(file_data_visible_to(request.user), id=file_id)
```

- `fcs_parser/permissions.py`: `experiments_visible_to(user)` (já filtra
  `active=True` — implementa o arquivamento do ADR-0005),
  `file_data_visible_to`, `uploads_visible_to`.
- `analytics/permissions.py`: `gates_visible_to` (gate alcançável quando a
  amostra pertence a experimento visível).
- Escrita exige além de visível: `require_can_edit_experiment` /
  `require_can_edit_file_data` / `require_can_edit_gate`, que levantam
  `PermissionDenied` (403). Visível-mas-não-editável é 403; invisível é 404.
- Toda view declara `permission_classes` explicitamente — nunca depender do
  default global. Endpoint público escreve `permission_classes = []` de
  propósito (padrão já usado em `accounts/`).
- `DELETE /experiment/<id>/` inativa (`active=False`) e exige dono ou admin
  na origem (`can_move_experiment`) — alinhado ao ADR-0005.

## Alternativas consideradas

### A) `DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]` no settings

Descartada como *única* defesa: protege quem esquece, mas esconde a decisão
e é fácil de furar com um `permission_classes = []` acidental. Mantemos a
regra "explícito por view"; o default global pode ser adicionado depois como
cinto-e-suspensório, sem substituir a declaração.

### B) `ObjectLevelPermission` do DRF (`has_object_permission`)

Descartada: exige buscar o objeto antes de checar (já vaza existência via
403) e duplica a regra de escopo em duas APIs diferentes. O queryset escopado
resolve leitura e existência num lugar só.

### C) Manter `check-hash` global

Descartada: o dedup informa `file_name` — metadado do blob de outra
organização. Escopado aos experimentos visíveis preserva a conveniência sem
vazar nada.

## Consequências

- Endpoint novo que esquecer o escopo vira IDOR silencioso — a revisão de PR
  precisa checar "qual queryset alimenta esse lookup?".
- `experiments_visible_to` virou ponto único: mudar a regra de visibilidade
  (ex.: papel read-only que não edita) passa a separar de fato
  `*_visible_to` de `can_edit_*` — hoje eles coincidem.
- Experimentos inativos somem de tudo: detalhe, listagem, amostras, gates,
  download. Reativar = recriar o recurso (ADR-0005).
- Validação dos endpoints de upload migrou para serializers
  (`ExperimentInitSerializer`, `ChunkUploadSerializer`,
  `ExperimentCompleteSerializer`, `ExperimentFileInitSerializer`), fechando a
  dívida do ADR-0009 no fluxo de upload; a resposta de erro mantém
  `{"detail": "..."}` via `_first_error`.
