# ADR-0031 — Faixa de exibição por canal vem dos metadados do instrumento

- **Status:** Proposto
- **Data:** 2026-10-13
- **Contexto do código:** `utils/density.py` (`default_scale`,
  `apply_scale`), `fcs_parser/services/process_fcs.py` (headers FCS),
  `pandora-front/src/features/plot/utils/sliders.ts` + `plotAxes.ts`
  (`LINEAR_SLIDER_MAX = 262144` fixo). PRDs BE-43/FE-44.

## Contexto

Os limites de eixo do scatter/densidade são constantes calibradas para
equipamentos de 18 bits (`262144`), enquanto o laboratório opera com
Cytoflex — FSC/SSC de linfócitos em linear na faixa de 2M–10M e
fluorescência em log até 10⁷. Populações reais estouram o teto do
eixo e o plot grampeia tudo na borda. A informação de faixa já existe
no arquivo: headers FCS declaram `$PnR` (faixa nativa do canal),
`$PnE` (amplificação log/linear), `$PnG` (ganho) e `$CYT` (modelo do
citômetro) — hoje capturados em `headers` no processamento e
descartados pelo consumidor.

## Decisão

**A faixa de exibição é propriedade do canal, lida dos metadados do
instrumento, não constante global por escala.**

- O processamento extrai por canal `range` (`$PnR`), `amplifier`
  (`$PnE`), `gain` (`$PnG`) e por arquivo `cytometer` (`$CYT`); fica
  persistido nos metadados da `FileData` e exposto no contrato de
  canais.
- O range/escala default de um eixo deriva desses metadados; canal sem
  metadado (amostras legadas) cai no comportamento atual
  (262144 + heurística FSC/SSC-linear/resto-biex).
- Escala de exibição (`linear`, `biex`, `log`) é ortogonal à faixa:
  o transform muda a função aplicada; o range define o teto. O
  `$PnE` informa a aquisição nativa do canal para o default da
  escala.
- Escolha manual do usuário (min/max no front) continua ganhando do
  default do canal — o metadado só muda o fallback, nunca sobrescreve
  a preferência.

## Alternativas consideradas

### A) Manter constante global e só subir o teto (ex.: 10M)

Descartada: achatar o teto pra todo mundo degrada o eixo dos arquivos
de 18 bits (população espremida em 2% do range) e repete o problema no
próximo equipamento. Constante não escala com heterogeneidade de
instrumentos — que é o caso real do laboratório.

### B) Hardcode por modelo de equipamento detectado (`$CYT`)

Descartada como fonte primária: depende de tabela mantida por nós,
errada quando o arquivo vem de instrumento fora da lista ou quando o
operador reconfigura os ranges do Cytoflex. `$PnR`/`$PnE` declaram a
verdade por canal — o modelo do equipamento fica como metadado
informativo, não como lookup de config.

### C) Derivar o range do máximo observado nos dados

Descartada como default: outliers/extremos de aquisição distorcem o
eixo e cada amostra ganharia teto diferente, quebrando comparação
lado a lado entre amostras do mesmo experimento. Útil como fallback
futuro quando `$PnR` estiver ausente, mas não como decisão principal.

## Consequências

- A fronteira passa a ser o metadado do arquivo: amostra nova já chega
  com a faixa certa, amostra legada segue no comportamento atual —
  retrocompatível por construção.
- Cria acoplamento novo ao formato FCS: headers malformados/ausentes
  (equipamentos que não preenchem `$PnR`/`$PnE` corretamente) viram
  fallback silencioso — monitorar amostras com `range: null` no uso.
- A estrutura de metadados por canal vira contrato: mudança futura na
  forma de persistir (JSON da FileData vs. coluna) segue expand-contract.
- Fica para refinamento: `log` com décadas do `$PnE` (`f,d` informa as
  décadas reais de aquisição, não só log/linear) e biex parametrizado
  por equipamento.
