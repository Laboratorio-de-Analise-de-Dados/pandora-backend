# BE-32 — Reuso de blob por hash no upload (dedup de storage)

**Repo:** pandora-backend · **Tipo:** feature/otimização · **Base:** `main`
**Status:** Visão — exige ADR próprio antes de implementar.
Conversa com [BE-10](BE-10-identidade-de-conteudo-e-blob-compartilhado.md)
(identidade de conteúdo), [BE-12](BE-12-dedup-no-upload.md) (dedup escopado
ao experimento, entregue) e ADR-0013 (blob compartilhado entre
experimentos).

## Problema

Hoje todo upload grava bytes novos em disco, mesmo quando o blob é
byte-a-byte idêntico a um já armazenado: `_get_or_create_upload` calcula
`sha256` mas não o usa para reuso. BE-12 descartou explicitamente o reuso
de blob cross-experiment no upload — o único compartilhamento de bytes é a
cópia explícita (BE-11).

O custo já é medido: na base de dev, 64 `guid`s duplicados são o mesmo
`.fcs` re-upado em experimentos diferentes — e cada re-upload duplica o
ZIP inteiro no storage, não só a amostra. Com o volume crescendo, dedup de
bytes vira otimização real, não prematura.

Padrão de mercado (CellEngine, Cytobank): dedup/reuso acontece **dentro do
boundary de acesso do usuário** (import-by-reference entre experimentos
visíveis), e dedup de bytes global, quando existe, é silencioso no
servidor — a literatura (Harnik et al., IEEE S&P 2010) mostra que dedup
cross-user com resposta ao cliente vira oráculo de existência e canal de
timing.

## Questões em aberto (insumo do ADR)

1. **Escopo do pool de blobs** — por organização (tenant), por
   visibilidade do usuário, ou global na instância? Global maximiza
   economia mas exige que nenhum sinal de "já existe" vaze para o cliente
   (oráculo de existência).
2. **Momento da deduplicação** — silencioso no `complete` (upload processa
   normal, servidor aponta para o blob existente e descarta os bytes
   novos; economiza storage, não bandwidth) vs. bloqueio via `check-hash`
   pre-flight (economiza bandwidth, mas só pode ser escopado ao que o
   usuário já vê — como hoje).
3. **Nível do reuso** — só `FileModel.file` (bytes do ZIP), ou também
   `parquet_path` compartilhado por `content_sha256`, ou até
   `FileDataModel` compartilhada entre experimentos (mudança de modelo)?
4. **Ciclo de vida** — deleção física só de blob sem referências exige
   contagem de referências (ADR-0013 já prevê); acopla com a rotina de
   retenção pendente (BE-10 §4: freeze/delete com garantia de entrega).

## Direção provável (a confirmar no ADR)

- `complete` calcula `sha256` → se `FileModel` com mesmo hash existe e o
  arquivo está em disco, o novo `FileModel` aponta para o `file` existente
  e os bytes recém-montados são descartados — mesmo padrão do
  `copy_experiment`, sem sinal visível ao cliente.
- `check-hash` continua escopado a `experiments_visible_to` — é a defesa
  contra o oráculo de existência; não expõe o pool global.
- `GUID`/`content_guid` segue como metadado de amostra e âncora de
  análise — nunca boundary de segurança nem constraint global.
- Limpeza física passa a exigir refcount — nunca apagar blob referenciado.

## Critérios de aceite

A definir após o ADR (escopo do pool, momento e nível do reuso são as
variáveis que mudam o contrato).

## Fora de escopo

- Dedup por amostra dentro do experimento — já entregue no BE-12
  (`guid`/`content_sha256`, skip + `skipped[]`).
- De-identificação de PHI nos headers (`$SRC`, `$SMNO`, keywords de
  paciente) — PRD próprio se/quando houver compartilhamento fora do
  tenant.
- Rotina de retenção/freeze completa (BE-10 §4) — este PRD só precisa da
  invariante "nunca apagar blob referenciado".
