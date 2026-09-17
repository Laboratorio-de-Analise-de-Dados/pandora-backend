# ADR-0012 — Identidade de conteúdo por `guid` (amostra) e `sha256` (blob)

- **Status:** Aceito — substitui parcialmente ADR-0006
- **Data:** 2026-09-13
- **Contexto do código:** `fcs_parser/models.py` (`FileModel`,
  `FileDataModel`), `fcs_parser/services/process_fcs.py`,
  `FileDataModel.headers`

## Contexto

ADR-0006 definiu `source_path` (caminho no ZIP) como identidade da amostra.
Funciona para "mesmo caminho = mesmo arquivo", mas não cobre os casos que o
BE-10/11/12 pedem: arquivo renomeado/movido no ZIP muda de `source_path` sem
mudar de conteúdo; e o `source_path` nada diz sobre o blob físico (o ZIP
inteiro) para dedup de storage.

Medido na base de dev: dos 320 arquivos com header, todos têm `guid` e zero
duplicados dentro do mesmo experimento — os 64 duplicados são o mesmo `.fcs`
re-upado em experimentos diferentes. Ou seja, `guid` já se comporta como
identidade de conteúdo na prática.

## Decisão

Duas identidades, cada uma no nível do objeto que ela descreve:

- `FileDataModel.content_guid` — coluna nova populada do keyword `guid` do
  header no parse; `UniqueConstraint(experiment, content_guid)` quando
  presente. É a âncora das referências de análise (propagação, histórico e
  rollback do BE-08) e da cópia de experimento.
- `FileModel.sha256` — hash do blob upado (ZIP ou `.fcs` solto), calculado no
  upload. Decide "mesmos bytes" para reutilização e dedup de storage.

`source_path` deixa de ser identidade: fica como dica de proveniência e
agrupamento do subsample inicial (a pasta do ZIP). Arquivo sem `guid` cai
nesse fallback.

## Alternativas consideradas

### A) Manter `source_path` como identidade única

Descartada. Rename/move no ZIP quebra a identidade sem mudar o conteúdo, e
não endereça o blob físico — dedup e cópia ficam sem base.

### B) `guid` como identidade do blob também

Descartada. `guid` é do `.fcs`; o blob é o ZIP container (muitos `.fcs`, um
blob). Níveis diferentes — a dedup de storage precisa de hash de bytes.

### C) Constraint funcional no JSON (`headers->>guid`)

Descartada. Funciona em Postgres puro, mas amarra identidade a expressão de
índice sobre JSON — coluna própria é mais legível, indexável e portável.

## Consequências

- Referências de análise podem mirar `content_guid` — sobrevive a rename e à
  cópia; BE-08 pode ancorar eventos nele.
- Upload ganha validação de duplicata real (guid dentro do experimento,
  sha256 entre blobs).
- **Dívida:** arquivos sem `guid` (poucos, depende do citômetro/software)
  ficam sem proteção de duplicata dentro do experimento — cobertura total só
  com hash por `.fcs` individual, se necessário no futuro.
- ADR-0006 passa a valer para proveniência/agrupamento; a parte "identidade"
  fica aqui.
