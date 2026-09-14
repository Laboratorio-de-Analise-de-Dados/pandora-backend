# BE-03 — Excluir gates aplicados em outras amostras (lote)

**Repo:** pandora-backend · **Item do doc:** 7 · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/bulk-delete-gates`
**Status:** Entregue no PR #69. Escopo explícito em [ADR-0002](../adr/0002-escopo-explicito-de-propagacao.md).

## Problema

`POST /analytics/gate/apply` copia uma estratégia de gates para N arquivos (estilo FlowJo), mas o inverso não existe: só `DELETE /analytics/gate/<gate_id>` (um gate por request). Desfazer um apply errado hoje exige apagar gate por gate, arquivo por arquivo.

## Escopo

Endpoint de exclusão em lote, espelhando a semântica do apply e usando o rastro `GateModel.copied_from`.

### Contrato

```
POST /analytics/gate/delete-batch
Auth: IsAuthenticated
Body:
{
  "source_gate_ids": [42],              // gates de origem (obrigatório)
  "scope": "file",                      // "file" (default) | "experiment"
  "target_file_data_ids": [10, 11],     // usado quando scope="experiment": restringe os arquivos alvo
  "recursive": true,                    // apaga sub-gates também (default true)
  "include_source": false               // apaga também o gate de origem (default false)
}

200 { "deleted": 8, "details": [{ "file_data_id": 10, "gate_ids": [51, 52] }] }
400 { "detail": "source_gate_ids é obrigatório." }
404 { "detail": "One or more source gates not found." }
```

### Regras

- Alvo = gates cujo `copied_from` é (transitivamente) um dos `source_gate_ids`. Resolver a cadeia de cópias, não só um nível — um apply em cadeia (A → B → C) deve ser alcançável a partir de A.
- **Escopo é explícito, nunca implícito** (decisão de 27/08): `scope="file"` (default) age só na amostra do gate; `scope="experiment"` age nas cópias dos outros arquivos do **mesmo experimento**, restringível por `target_file_data_ids`. Não propagar para outros experimentos — `copied_from` não cruza experimento. Quem decide é o usuário, no diálogo do front (FE-05).
- `recursive=true` → apagar descendentes (`parent` é CASCADE, então basta apagar o topo; documentar isso). `recursive=false` → só o nível correspondente, reatribuindo os filhos ao parent do gate apagado.
- `include_source=false` (default) preserva a origem: a intenção do item é "limpar o que foi replicado nas outras amostras".
- Quadrantes: mesma auto-expansão do `ApplyGateView` — selecionar um quadrante inclui os 4 irmãos do grupo.
- Arquivos desativados (BE-01, `active=False`) são ignorados no lote por padrão — os gates deles continuam intactos.
- Autorização: o usuário precisa ter acesso ao experimento dos gates (mesmo helper de escopo do BE-01/BE-02). Rejeitar 403 se algum gate alvo estiver fora do escopo.
- Invalidar densidade dos arquivos afetados (`utils.density.invalidate_density`) e rodar dentro de `transaction.atomic()`.
- Reaproveitar/extrair helpers do `ApplyGateView` (resolução de ancestralidade/cópias) para `analytics/services.py` em vez de duplicar lógica na view.

## Arquivos a tocar

- `analytics/views.py` — `DeleteGateBatchView(APIView)`.
- `analytics/urls.py` — `path("gate/delete-batch", ...)`.
- `analytics/services.py` (novo, opcional) — helpers compartilhados com o apply.
- `analytics/tests/`.

## Critérios de aceite

- [ ] Aplicar um gate em 3 arquivos, chamar delete-batch com o gate de origem → as 3 cópias somem, a origem permanece.
- [ ] `include_source=true` → a origem também é apagada.
- [ ] `scope="file"` (default) **não** toca nas cópias das outras amostras.
- [ ] `target_file_data_ids` restringe corretamente (as cópias dos outros arquivos ficam).
- [ ] Gates de arquivo desativado não são apagados.
- [ ] Sub-gates das cópias somem com `recursive=true`.
- [ ] Grupo de quadrante é apagado inteiro quando um quadrante é passado.
- [ ] Usuário sem acesso ao experimento → 403 e nada apagado.
- [ ] Densidade dos arquivos afetados invalidada.
