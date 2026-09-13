# BE-12 — Dedup no upload: aviso e reutilização de blob

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Status:** Implementado na branch `feat/file-identity-copy`. Depende de
[BE-10](BE-10-identidade-de-conteudo-e-blob-compartilhado.md) (`sha256` no
`FileModel`). UI no front: FE-16.

## Problema

Upload chunked hoje não olha se o arquivo já existe no servidor — o usuário
sobe o mesmo ZIP inteiro de novo e ganha um blob duplicado. Com `sha256` no
`FileModel` (BE-10), dá pra detectar antes e reusar.

## Escopo

### 1. Check antes de subir

```
POST /experiment/check-hash/
Body: { "sha256": "<hash do arquivo>" }

200 { "exists": true | false, "file_name": "..."? }
```

- O front calcula o hash do arquivo localmente (Web Crypto) **antes** de
  iniciar o chunked upload. Se `exists`, pergunta ao usuário: reutilizar ou
  subir mesmo assim.
- Reuso cria um `FileModel` novo pro experimento apontando pro **mesmo
  caminho** do blob existente — o experimento nasce sem upload de fato.

### 2. Reuso explícito no `complete/`

- `complete/` aceita `sha256` + `reuse: true`: sem chunks, cria o
  `FileModel` do novo experimento apontando pro caminho do blob doador.
- Sem `reuse`, o fluxo monta os chunks e grava o `sha256` do blob
  resultante — reuso nunca é silencioso: a decisão é sempre do usuário no
  diálogo do front.

### 3. Dedup por amostra (server-side, pós-extração)

Decisão: **o upload continua sendo o ZIP inteiro** — sem unzip/pre-scan no
cliente; o servidor gerencia tudo. Por isso a dedup por `.fcs` acontece na
extração, não antes do upload:

- `FileDataModel.content_sha256` (BE-10) identifica cada amostra; o
  `check-hash` consulta também essa coluna, então um `.fcs` solto bate com
  amostra dentro de um ZIP anterior.
- Pós-extração, comparar `content_sha256` das amostras novas com o restante
  da base permite informar "N amostras já existiam em outros experimentos".

### Regras

- `sha256` calculado uma vez no upload e persistido em `FileModel`
  (populado também por backfill na migração do BE-10).
- Reuso nunca altera o blob existente — só cria referências novas.
- `guid`+metadados (`cyt`/`date`/`inst`/`tot`) ficam como camada de
  confirmação metodizável no diálogo do front — o hash decide os bytes.

## Arquivos a tocar

- `fcs_parser/views.py` + `urls.py` — `CheckHashView`; `complete/` com reuse
- `fcs_parser/services/` — extração/persistência do `sha256`

## Critérios de aceite

- [x] Check-hash responde exists/false corretamente (blob e amostra).
- [x] Reuso cria experimento funcional sem bytes novos no storage.
- [x] Blob reutilizado permanece íntegro e intocado.
- [ ] Aviso pós-extração de amostras já existentes (`content_sha256`).

## Fora de escopo

- Manifesto por `.fcs` no upload (pre-scan no cliente) — **descartado**: o
  upload segue sendo o ZIP inteiro e o servidor gerencia a extração/dedup.
- UI — FE-16 no pandora-front.
