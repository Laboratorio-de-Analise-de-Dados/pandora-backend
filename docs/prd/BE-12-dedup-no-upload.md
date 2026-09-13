# BE-12 — Dedup por experimento e upload incremental

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Status:** Implementado na branch `feat/file-identity-copy`. Depende de
[BE-10](BE-10-identidade-de-conteudo-e-blob-compartilhado.md). UI no front:
FE-16.

## Problema

Um experimento precisa aceitar arquivos novos ao longo da vida — o cliente
"adiciona citometria" a uma análise existente, não só na criação. E a
deduplicação que importa é a pergunta "este arquivo já está **neste**
experimento?" — validação cross-experiment foi descartada: complicava o
modelo sem ganho real pro fluxo do cliente, e o escopo por experimento
também simplifica a limpeza (cada upload pertence a um experimento).

## Escopo

### 1. Experimento pode nascer vazio; arquivos entram depois

Uploads são `FileModel`s ligados ao experimento por FK
(`related_name="uploads"`). O fluxo de criação (`init/ → upload-chunk/ →
complete/`) continua igual; para anexar arquivos depois:

```
POST /experiment/<id>/files/init     → cria FileModel reserva → { fileId }
POST /experiment/files/upload-chunk/ → chunks namespaced por "f<fileId>"
POST /experiment/files/complete      → monta, hasheia, extrai
                                       → { status, added, skipped[] }
```

Permissão: `can_edit_experiment` nas três pontas.

### 2. `.fcs` solto vira ZIP no servidor

A unidade física é sempre ZIP: upload `.fcs` é aglutinado
(`wrap_fcs_as_zip`) antes da extração — storage uniforme e a mesma rotina
de rebuild/download serve pra tudo.

### 3. Dedup escopado ao experimento

- `check-hash` aceita `experiment_id` opcional: com ele, responde "este
  arquivo já está **neste** experimento" (olha `FileModel.sha256` dos
  uploads e `FileDataModel.content_sha256` das amostras). Sem ele, a
  resposta é global/informativa.
- Na extração, amostra com `content_guid` **ou** `content_sha256` já
  presente no experimento é **pulada** e reportada em `skipped` — aviso,
  nunca bloqueio, nunca reuso silencioso.
- Cross-experiment não é dedup automático: subir o mesmo `.fcs` em outro
  experimento cria amostra independente; compartilhar blob é só via cópia
  explícita (BE-11).

### 4. Download derivado

```
GET /experiment/<id>/download → ZIP reconstruído na hora:
    <subsample.name>/<arquivo>.fcs   (raiz quando sem subsample)
```

Cada amostra é lida do **seu** upload (`fd.file` + `source_path`), então o
download reflete a organização atual e mistura origens transparentemente.
O blob guardado continua sendo o freezer; a saída é artefato derivado.

## Arquivos a tocar

- `fcs_parser/models.py` — `FileModel.experiment` OneToOne→FK,
  `total_chunks`/`received_chunks` por upload
- `fcs_parser/views.py` + `urls.py` — endpoints `files/*`, `download`,
  `check-hash` com `experiment_id`
- `fcs_parser/services/process_experiment_file.py` — wrap, dedup skip,
  resolução por upload

## Critérios de aceite

- [x] Adicionar ZIP ou `.fcs` a experimento existente funciona com o mesmo
      protocolo de chunks.
- [x] `.fcs` solto é persistido como ZIP de uma entrada.
- [x] Amostra duplicada no mesmo experimento é pulada e reportada.
- [x] Mesma amostra em outro experimento é permitida (dedup por escopo).
- [x] Download sai organizado pelos subsamples atuais.
- [x] Cada `FileDataModel` resolve o `.fcs` no upload certo (`fd.file`).

## Fora de escopo

- Manifesto por `.fcs` no upload (pre-scan no cliente) — **descartado**.
- Reuso de blob cross-experiment no upload — **descartado**; só cópia
  explícita (BE-11) compartilha bytes.
- Verificação de "batelada" (mesmo `cyt`/`date`/`btim` entre amostras de
  um experimento → aviso de batch diferente) — futuro, os headers já estão
  persistidos.
