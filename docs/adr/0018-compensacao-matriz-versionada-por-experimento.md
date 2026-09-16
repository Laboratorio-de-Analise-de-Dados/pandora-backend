# ADR-0018 — Compensação como matriz versionada por experimento, aplicada na leitura

- **Status:** Aceito
- **Data:** 2026-09-15
- **Contexto do código:** `fcs_parser/models.py` (ExperimentModel,
  FileDataModel.headers com keywords FCS crus), `utils/density.py`
  (compute_density, cache por amostra), `analytics/` (gate stats,
  AnalysisRevision do BE-08)

## Contexto

Citometria de fluxo multicolor tem spillover espectral: um fluorócromo
vaza sinal nos detectores dos outros. Sem compensação, gates em canais de
fluorescência ficam enviesados. O arquivo .fcs pode trazer uma matriz de
spillover embutida (keyword `$SPILLOVER` no FCS 3.x; `$COMP` em versões
antigas), mas ela pode estar ausente ou errada — o padrão da área é
recalcular a partir de controles single-stain + negativo.

Hoje o Pandora ignora compensação por completo: headers são guardados crus
(`result.headers` no parse) e nenhuma etapa de leitura transforma os
eventos. Guardar eventos já compensados quebraria a fonte da verdade
(ADR-0004: ZIP é L0, Parquet é cache regenerável) — reprocessar ou trocar
de matriz exigiria reescrever dado.

## Decisão

Compensação vira uma **entidade por experimento** (`CompensationMatrix`):
matriz N×N em JSON + ordem de canais + origem (`fcs_header`, `computed`,
`manual`) + `active` (soft delete, ADR-0005). Um experimento pode ter
várias matrizes (ex.: a do instrumento e uma recalculada); **no máximo uma
é a ativa** — aplicada na camada de leitura.

Aplicar = multiplicar o vetor de eventos dos canais fluorescentes pela
**inversa** da matriz de spillover, dentro de `compute_density` /
estatísticas de gate / scatter — **antes** do binning e da escala de
exibição. Dado bruto nunca é alterado: ZIP e Parquet continuam crus, e
trocar/retirar a compensação é instantâneo (invalida o cache de densidade
da amostra, que já é versionado por arquivo).

Trocar ou remover a compensação ativa muda resultados de gates — portanto
é registrada como **AnalysisRevision** (BE-08), mantendo a linha de
análise auditável e revertível. Compensação é parâmetro de análise, não
metadado silencioso.

## Alternativas consideradas

### A) Compensar na escrita (parquet compensado)

Descartada: duplica storage ou destrói o dado cru; trocar de matriz
exigiria rebuild de todos os parquets. Fere ADR-0004 (Parquet é cache,
não verdade).

### B) Matriz única por arquivo .fcs

Descartada para a v1: controles e matriz são propriedade do experimento
(o mesmo painel); per-file só faria sentido se o instrumento gerasse
matrizes distintas por tubo — raro. Nada impede evoluir depois.

### C) Confiar sempre no `$SPILLOVER` embutido

Descartada: nem todo arquivo traz a keyword, e quando traz pode estar
desatualizada em relação aos controles do dia. O usuário precisa poder
recalcular — daí a origem `computed` no BE-22.

## Consequências

- Facilita: experimentar matrizes sem reprocessar upload; auditoria do
  "quando a compensação mudou" na timeline; revert desfaz a troca.
- Dívida: a inversa N×N roda por amostra na leitura — matrizes grandes
  (>20 canais) têm custo mensurável; mitigar com cache (a chave de
  densidade precisa incluir a identidade da matriz ativa).
- Exige: chave de cache de densidade incorporar `compensation_id` —
  caso contrário o FileBasedCache serviria densidade compensada como se
  fosse crua.
- Não cobre: espectral unmixing (full-spectrum cytometry) — modelo de
  referência diferente, fora de escopo.
