# BE-09 — Expor metadados do header FCS por amostra

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `chore/ai-setup`
**Status:** Entregue na branch `chore/ai-setup` (commit `3b54728`), PR pendente.

## Problema

`FileDataModel.headers` (JSONField) já guarda todos os keywords do header FCS —
capturados por `readfcs.view()` no processamento e normalizados por
`transform_key()`/`serialize_value()`. Mas o dado ficava preso no banco: nenhum
endpoint o expunha, então o front não conseguia mostrar metadados como data de
aquisição (`$date`), equipamento (`$cyt`) ou total de eventos (`tot`).

## Escopo

Endpoint read-only que devolve o dict completo de headers de uma amostra. Sem
filtro de chaves no backend — o front decide o que exibir com label amigável e
o que vai para a lista bruta, então nada do header é descartado na borda da API.

### Contrato

```
GET /experiment/file/<file_id>/headers

200 {
  "file_data_id": 12,
  "file_name": "tempo1_A01.fcs",
  "headers": { "$date": "...", "$cyt": "...", "tot": 50000, ... }
}
404 amostra inexistente ou de outro experimento do usuário
```

### Regras

- View simples (`APIView` + `get_object_or_404` com o mesmo filtro de
  autorização por experimento usado nas outras views de arquivo); o `headers`
  já é JSON nativo, não precisa de serializer.
- Read-only: metadados vêm do arquivo FCS, não são editáveis.
- Amostra inativa também responde — o header é do arquivo, não do estado.

## Arquivos a tocar

- `fcs_parser/views.py` (`FileHeadersView`)
- `fcs_parser/urls.py` (rota `file/<int:file_id>/headers`)

## Critérios de aceite

- [x] `GET` retorna `file_data_id`, `file_name` e `headers` completos.
- [x] `manage.py check` e checagem de sintaxe ok (validado no container).
- [ ] Resposta validada contra arquivo real com `$date`/`$cyt`/`tot`
      (verificação manual no front, FE-11).

## Fora de escopo

- Edição de metadados, upload com header sobrescrito, listagem agregada de
  headers por experimento.
