# ADR-0029 — Figuras de análise persistidas: spec + cache regenerável + âncora de procedência

- **Status:** Aceito
- **Data:** 2026-09-29
- **Contexto do código:** `analytics` (`AnalysisFigure`, `AnalysisRevision`,
  `AnalysisBranch`), endpoints `/analytics/*/figures/`

## Contexto

O citometrista monta gráficos comparativos fora do app (Prism/Excel). Uma
figura renderizada on-the-fly responderia a pergunta visual, mas não a de
confiança: "esses dados vieram de qual estado da análise?". Re-gating
posterior não pode corromper silenciosamente a figura que vai pro
relatório — e re-computar tudo a cada abertura seria custo de servidor
sem necessidade.

## Decisão

A figura é um **artefato persistido** (`AnalysisFigure`) composto de três
peças:

1. **`spec`** — a receita (grupos de `file_data_ids`, populações por
   caminho de nomes, métrica, canal). É o que o usuário editou.
2. **`result_cache`** — o produto computado, **efêmero e regenerável**
   (mesma filosofia do Parquet, L2 do ADR-0004): pode ser descartado e
   re-gerado a partir do spec. **Sem tabela de histórico de resultados**
   — a trilha de mudança dos dados já é o `AnalysisRevision` (append-only,
   ADR-0008), que registra quem mudou, o quê, e permite `revert`.
   Versionar resultados duplicaria a timeline.
3. **`result_revision`** — FK para a `AnalysisRevision` head **no momento
   do cômputo** — carimbo de procedência, não vínculo bloqueante.

**Staleness por fingerprint**: `result_cache.resolved_inputs` guarda os
alvos resolvidos no cômputo (`gate_ids`, `file_data_ids`, `channel`).
`is_stale` = existe `AnalysisRevision` mais recente que `result_revision`
cujo `target_type`/`target_id` (ou `affected_ids`) intersecta o conjunto
resolvido — **ou** ação experiment-wide (`apply`, `merge`, `derive`,
`compensation_apply`, `compensation_remove`, `move_subsample`), que pode
mudar stats de qualquer gate. Renomear um gate que a figura não usa não
marca stale.

**Resolução quebrada nunca é silenciosa**: população que não resolve mais
(rename) ou amostra que sai do grupo entram em `unmatched` no cache, e o
`recompute/` devolve `removed_since_last` — o front confirma antes de o
usuário aceitar a figura nova.

**CRUD de figura não grava `AnalysisRevision`** — a timeline é da
estratégia de análise; figura é artefato de relatório. `recompute/` só
move a âncora `result_revision`.

**`published=true`** trava mutação (`PATCH` de spec e `recompute/` →
409): não é assinatura legal, é proteção contra mudança acidental em
figura que já saiu em relatório.

**`branch` FK null no v1** — a tabela `AnalysisBranch` já existe (custo
zero), mas a figura é do experimento; semântica por-branch é decisão
futura, habilitada pelo campo já presente.

## Alternativas consideradas

### A) Figura efêmera — recomputa a cada render

Sem persistência: front sempre pede os dados agregados. Descartada —
não responde "de qual estado da análise vieram os dados"; re-gating
muda a figura do relatório sem aviso. O requisito de produto é
confiabilidade, não só visualização.

### B) Tabela de histórico de resultados (uma linha por cômputo)

Guardaria cada `result_cache` versionado. Descartada — duplica o papel
do `AnalysisRevision` (a mudança nos dados já é trilhada e revertível
lá); o que a figura precisa guardar é a receita e a âncora, não cada
produto intermediário.

### C) Gate_ids imutáveis no spec em vez de caminhos de nomes

Mais robusto a rename, mas spec vira opaco pra humano e quebra se o
gate for recriado. Descartada — caminho de nomes + `unmatched` cobre o
caso com informação legível; o fingerprint usa os `gate_ids` *resolvidos*
(não os do spec), então a detecção de staleness é por id mesmo assim.

### D) Staleness por "qualquer revisão nova no experimento"

Simples, mas barulhento — figura ficaria stale por edição em gate
não relacionado. O fingerprint por alvos resolvidos mantém o sinal.

## Consequências

- **Mais fácil:** export confiável (o CSV carimba `result_revision`/
  `computed_at`); figura publicada é estável por definição; recompute
  explícito dá ao usuário controle de quando absorver dados novos.
- **Mais difícil / a monitorar:** a resolução por caminho de nomes é
  frágil a rename — mitigada por `unmatched`/`removed_since_last`, mas
  uma figura pode envelhecer "quebrada" até o usuário recomputar (por
  design: recompute é manual). `is_stale` depende de `AnalysisRevision`
  registrar `affected_ids` corretamente — revisão que não declara seus
  alvos não marca stale.
- **Dívida assumida:** semântica de `branch` (figura por linha de
  análise?) fica em aberto; `distribution` tem cache só de metadados —
  as curvas vêm do density live, então sua "procedência" é parcial (a
  âncora vale, mas a curva renderizada é sempre o estado atual).
