# BE-12 — Dedup no upload: aviso e reutilização de blob

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `chore/ai-setup`
**Status:** Proposto. Depende de
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
- Reuso cria `FileDataModel`s novos apontando pro `FileModel` existente —
  o experimento nasce sem upload de fato.

### 2. Fallback no `complete/`

- Se o check não rodou (cliente antigo, race), o `complete/` calcula o hash
  do blob final e reutiliza o `FileModel` existente mesmo assim — a
  confirmação do usuário fica implícita no upload; resposta marca
  `"reused": true` pro front mostrar o aviso depois.

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

- [ ] Check-hash responde exists/false corretamente.
- [ ] Reuso cria experimento funcional sem bytes novos no storage.
- [ ] Upload sem check prévio ainda deduplica no `complete/`.
- [ ] Blob reutilizado permanece íntegro e intocado.

## Fora de escopo

- Dedup de `.fcs` **dentro** do ZIP (o hash é do blob; dedup interno usa
  `content_guid` — BE-10).
- UI — FE-16 no pandora-front.
