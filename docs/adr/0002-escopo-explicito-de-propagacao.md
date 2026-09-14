# ADR-0002 — Propagação de gate exige escopo explícito do usuário

- **Status:** Aceito
- **Data:** 2026-08-27
- **Contexto do código:** `analytics/gate_scope.py`, `analytics/views.py` (`scope`, `dry_run`)

## Contexto

Renomear, recolorir, excluir ou remodelar um gate que foi aplicado em N amostras
é ambíguo: o usuário pode querer corrigir só a amostra que está olhando ou
corrigir o grupo todo. Adivinhar erra em metade dos casos, e nos dois sentidos o
erro é destrutivo (ou o grupo fica inconsistente, ou o ajuste manual de uma
amostra é sobrescrito).

## Decisão

Todo endpoint que pode afetar mais de um gate recebe
`scope: "file" | "experiment"`, com **default `"file"`** (o menor efeito), e o
front pergunta antes ("apenas nesta amostra" / "em todas as amostras"). Quando a
operação pode sobrescrever algo, o endpoint aceita `dry_run=true` e devolve o que
seria afetado, para a UI confirmar com números reais em vez de um aviso genérico.

## Alternativas consideradas

### A) Propagar sempre para a família de cópias

Descartada: transforma qualquer ajuste fino em uma alteração global; o usuário
perde o ajuste manual de amostras específicas sem ter pedido nada.

### B) Nunca propagar (o usuário repete a operação amostra por amostra)

Descartada: é exatamente a dor relatada no teste — 20 amostras, 20 renomeações
manuais.

### C) Preferência global por usuário ("sempre aplicar a todas")

Descartada por ora: esconde a decisão no momento em que ela importa e a
consequência (sobrescrita) é irreversível hoje. Pode voltar quando existir
histórico/rollback (ADR-0008).

## Consequências

- Nenhuma operação em lote acontece sem intenção declarada.
- Mais cliques para quem sempre quer o grupo todo.
- `dry_run` duplica a ida ao servidor nas operações destrutivas — aceitável
  porque a alternativa é confirmar no escuro.
