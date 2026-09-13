# BE-04 — Propagar nome e cor do gate para as cópias

**Repo:** pandora-backend · **Item do doc:** 9 · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/propagate-gate-name-color`
**Status:** Entregue no PR #69. Escopo explícito em [ADR-0002](../adr/0002-escopo-explicito-de-propagacao.md); família de propagação em [ADR-0003](../adr/0003-linhagem-de-gates-por-copied-from.md).

## Problema

`UpdateGateView.patch` altera `name`/`color` **somente no gate alvo**. Como o apply cria cópias independentes por arquivo, renomear ou recolorir um gate deixa as outras amostras com nome/cor antigos — inviabilizando comparação e leitura do relatório.

## Escopo

Permitir que um PATCH de `name`/`color` se propague para os gates equivalentes nas outras amostras.

### Contrato

```
PATCH /analytics/gate/<gate_id>
Body: { "name": "CD4+", "color": "#ff0000", "scope": "experiment" }
      // scope: "file" (default, só este gate) | "experiment" (cópias nas outras amostras)

200 → GateSerializer do gate alvo + { "propagated_gate_ids": [51, 52] }
400 { "detail": "Nome já usado neste nível." }
```

### Regras

- **Escopo explícito, escolhido pelo usuário** (decisão de 27/08): `scope="file"` é o default e mantém o comportamento atual (só o gate alvo). `scope="experiment"` propaga. O front pergunta no diálogo (FE-03) — nunca decidir por ele.
- Alvo da propagação: gates ligados ao alvo pela cadeia de `copied_from` (nos dois sentidos: irmãos-cópias da mesma origem e cópias do alvo). Ou seja: subir até a raiz da cadeia de cópias e descer aplicando em todos.
- Limite superior do escopo: o **mesmo experimento**. Propagar entre experimentos exigiria match por caminho de nomes (o `copied_from` não cruza experimento) — fora de escopo, registrar issue se surgir a necessidade.
- Cópias em arquivos desativados (BE-01) são ignoradas.
- `name`: respeitar as constraints `unique_gate_name_per_parent` e `unique_gate_name_root_level`. **Política definida (12/09):** a decisão é do usuário, não do backend — o endpoint renomeia onde consegue e devolve `conflicts` com as amostras que colidiram; o front avisa antes (`dry_run`) de que dados na mesma hierarquia de outra amostra seriam sobrescritos. A alternativa de abortar tudo em 400 foi descartada por travar o caso comum por causa de uma amostra.
- `color`/`name` não mexem em geometria: manter o comportamento atual de **não** recalcular análise nem invalidar densidade nesse caminho.
- Tudo dentro de `transaction.atomic()`; devolver a lista de ids afetados pro front invalidar o cache certo.
- Extrair a resolução da cadeia de cópias para `analytics/services.py` — mesmo helper que o BE-03 usa. Se os dois MRs andarem juntos, o primeiro a entrar cria o módulo.

## Arquivos a tocar

- `analytics/views.py` — `UpdateGateView.patch`.
- `analytics/services.py` — `resolve_copy_family(gate)`.
- `analytics/tests/`.

## Critérios de aceite

- [ ] PATCH `{name, scope: "experiment"}` → gate alvo e todas as cópias no experimento com o nome novo; response lista os ids.
- [ ] PATCH sem `scope` (ou `scope: "file"`) → só o gate alvo muda (comportamento atual preservado).
- [ ] Mesmo para `color`.
- [ ] Colisão de nome em um arquivo alvo → as demais são renomeadas e a response lista `conflicts`.
- [ ] PATCH de `gate_coordinates` continua recalculando análise/invalidando densidade e não propaga geometria.
