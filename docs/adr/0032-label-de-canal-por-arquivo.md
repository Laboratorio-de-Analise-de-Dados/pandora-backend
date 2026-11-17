# ADR-0032 — Label de canal é metadado por arquivo, com `$PnS` como default

- **Status:** Proposto
- **Data:** 2026-11-17
- **Contexto do código:** `fcs_parser/services/channel_meta.py`
  (extrai `$PnN`/`$PnS`/`$PnR`/`$PnE`/`$PnG`), `FileDataModel`,
  `pandora-front/.../StatsPanel.tsx` (labels em `localStorage`
  `pandora_channel_labels`), `scatter-plot/index.tsx` (eixo com nome
  cru do canal). PRDs BE-47/FE-51.

## Contexto

O citometrista nomeia canais pelo fluoróforo real do painel (ex.:
"F4/80" em FL11-A) e a renomeação não funciona: o label vive só no
`localStorage` de um painel do front — não persiste entre
dispositivos, não é compartilhado e nunca chega ao eixo do plot, que
mostra o nome cru `$PnN`. O próprio arquivo já declara o stain no
header (`$PnS`), extraído no processamento e hoje ignorado pela UI.

## Decisão

**O label de canal é metadado por arquivo — mapa `canal → label` na
`FileData` — com precedência de exibição `label custom > $PnS >
$PnN`.**

- Escopo por arquivo, não por experimento nem organização: o mesmo
  canal físico (FL11-A) pode ter stains diferentes entre amostras do
  painel.
- `$PnS` é o default de exibição quando o arquivo declara; o label
  custom sobrescreve sem reescrever o metadado extraído.
- O nome cru (`$PnN`) nunca é alterado — gates, densidade e
  compensação referenciam o canal físico, não o rótulo.

## Alternativas consideradas

### A) Label só no cliente (localStorage)

Descartada — é o bug atual: não persiste, não compartilha e fica
invisível para o plot.

### B) Label por canal do experimento

Descartada como escopo primário: amostras do mesmo experimento podem
ter stains diferentes; agrupar por experimento gravaria rótulo errado
em parte das amostras. Propagação explícita entre amostras pode virar
follow-up, mas como ação do usuário, não como modelo.

### C) Reescrever `channel_meta.stain` com o label custom

Descartada: mistura dado do instrumento (`$PnS`, fato do arquivo) com
preferência do usuário — e impede distinguir "veio do FCS" de "veio
do usuário" na UI e em auditoria.

## Consequências

- Labels sobrevivem a sessão/dispositivo e valem em plot, stats e
  qualquer consumidor do payload da amostra.
- `FileData` ganha campo JSON editável — expand-contract na migration;
  shape do mapa vira contrato com o front.
- Propagação entre amostras ("mesma configuração de painel") fica
  como follow-up explícito — decisão registrada, não implementada.
- Canal renomeado no instrumento (`$PnN` muda entre aquisições) perde
  o label — a chave é o nome cru; aceito por ora.
