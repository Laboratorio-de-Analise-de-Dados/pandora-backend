# ADR-0016 — Canal ausente torna o gate não-avaliável e corta a linhagem na amostra

- **Status:** Proposto
- **Data:** 2026-09-14
- **Contexto do código:** `utils/density.py` (`apply_gate_filter`),
  `analytics/tasks.py` (`recalculate_gate_analysis`), `GateDensityView` /
  `GetGateDataView`, `AnalysisResult`

## Contexto

Painéis de canais divergem entre aquisições: um gate FITC-A × APC-A aplicado
numa amostra que não tem esses parâmetros não pode ser avaliado ali. Hoje
`apply_gate_filter` trata "não consegui avaliar" como "sem filtro" e devolve o
dataset intacto — o gate passa a contar **100% do parent** e todos os
descendentes herdam a stat fictícia. No teste de 14/09 isso se manifestou como
"Erro ao carregar dados" no plot e, pior, números errados na árvore/relatório.

O referencial do usuário é o FlowJo: a estratégia de gating só vale até onde os
parâmetros existem — abaixo disso a população não existe para aquela amostra.

## Decisão

Um gate cujo canal não existe na amostra é **não-avaliável** ali, e a
não-aplicabilidade **desce a subárvore**: nenhum descendente é avaliado, porque
seu parent não produziu população. Não se grava stat fictícia — o
`analysis_result` recebe um marcador explícito
(`{ applicable: false, reason: "missing_channels", missing_channels: [...] }`),
distinguindo "não avaliável" de "ainda não calculado" (ausência) e de
"filtrou tudo" (0 eventos, estado válido).

O gate continua existindo na amostra (aparece na árvore/relatório marcado como
não-aplicável) — não se apaga nem se desanexa: a linhagem `copied_from`
(ADR-0003) não é afetada por painel divergente.

## Alternativas consideradas

### A) Manter o no-op silencioso

Descartada: é o bug — 100% do parent com cara de stat real.

### B) Sinalizar mas continuar avaliando descendentes

Descartada: descendente de população inexistente não tem semântica; "burlar o
parent" produziria números sobre o dataset inteiro, a mesma mentira um nível
abaixo.

### C) Não criar a cópia / desanexar quando a amostra não tem o canal

Descartada: o gate é da estratégia do painel; a amostra é que diverge. Sumir
com o gate esconderia a divergência em vez de expô-la, e quebraria a árvore
comparada entre amostras.

## Consequências

- `apply_gate_filter` precisa de um terceiro estado (não-avaliável ≠ vazio);
  chamadores (density, stats, relatório) tratam cada um diferente.
- Front ganha um estado novo para renderizar (FE-22) — "—"/causa em vez de
  número.
- Stats antigas gravadas pelo no-op permanecem até recálculo; não há migração
  retroativa (BE-18).
- Se no futuro existir mapeamento de canais entre aquisições ($PnS/$PnN), a
  avaliação passa a consultar o mapeamento antes de declarar não-aplicável —
  este ADR não impede.
