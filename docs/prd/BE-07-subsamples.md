# BE-07 — Subsamples: caminho do ZIP como identidade e escopo de análise

**Repo:** pandora-backend · **Item do doc:** — (levantado em 12/09) · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/subsamples`
**Status:** partes 1 (identidade + CRUD) e 2 (subsample como escopo de propagação) no PR #78.
**ADRs:** [0006](../adr/0006-identidade-de-amostra-e-subsample.md), [0002](../adr/0002-escopo-explicito-de-propagacao.md), [0005](../adr/0005-api-nunca-deleta.md)

## Problema

A extração do ZIP guardava só o basename (`a1.fcs`) e descartava as subpastas.
Consequências:

1. `tempo_1/a1.fcs` e `tempo_2/a1.fcs` são amostras diferentes do mesmo
   experimento, mas ficavam indistinguíveis;
2. a reextração do FCS casava por sufixo do nome, então podia devolver o arquivo
   da pasta errada;
3. "aplicar gate em todas as amostras" só sabia o escopo `experimento`, embora
   controles e condições normalmente se refiram a um subconjunto.

## Escopo

### Parte 1 — identidade e CRUD (entregue)

- `FileDataModel.source_path` = caminho relativo dentro do ZIP; unicidade
  `(experiment, source_path)` para caminhos não vazios.
- `SubsampleModel(experiment, name, source_path, created_at, created_by, active)`
  com unicidade de `name` por experimento; `FileDataModel.subsample` FK nullable.
- A engine cria um subsample por diretório do ZIP (`get_or_create` por
  `source_path`); arquivo na raiz fica sem subsample.
- Reextração do FCS casa caminho exato e só cai no sufixo para dados legados.
- Migration de backfill: `source_path = file_name` quando o nome é único no
  experimento; homônimos antigos ficam vazios.
- `manage.py repair_source_path [--experiment N] [--recreate] [--dry-run]`
  redescobre o caminho abrindo o ZIP: nome único no ZIP é remapeado preservando
  a linha (gates e Parquet intactos); nome repetido é ambíguo e só com
  `--recreate` as linhas antigas são inativadas e as amostras recriadas a partir
  do ZIP, assumindo a perda das análises delas
  ([ADR-0006](../adr/0006-identidade-de-amostra-e-subsample.md)).

#### Contrato

```
GET    /experiment/<experiment_id>/subsamples/?include_inactive=true
POST   /experiment/<experiment_id>/subsamples/        { "name": "controles" }
PATCH  /experiment/<experiment_id>/subsamples/<pk>/   { "name": "tempo 1" }
DELETE /experiment/<experiment_id>/subsamples/<pk>/   → inativa e desvincula as amostras
PATCH  /experiment/file/<file_id>/subsample           { "subsample": 12 | null }
```

- `SubsampleSerializer` expõe `id`, `name`, `source_path` (read-only), `active`,
  `created_at`, `files_count` (só amostras ativas).
- Autorização por experimento (`can_edit_experiment`); subsample de outro
  experimento em `PATCH .../subsample` → 400.
- Nome duplicado → 400 (`IntegrityError` traduzido), não 500.

### Parte 2 — subsample como escopo

- `scope` passa a aceitar `"subsample"` nos endpoints que já têm
  `"file" | "experiment"`: exclusão em lote, nome/cor e geometria.
- `"subsample"` = amostras ativas do mesmo subsample da amostra alvo; amostra
  sem subsample cai em `"file"`.
- `PATCH /analytics/gate/<id>` aceita `dry_run`: nada é gravado e a resposta
  devolve `applied_scope`, `conflicts` e `affected` (gate, amostra,
  `source_path` e campos que mudariam), para a confirmação da UI listar as
  amostras que seriam sobrescritas.
- A família de `copied_from` continua sendo o conjunto de candidatos
  ([ADR-0003](../adr/0003-linhagem-de-gates-por-copied-from.md)); o escopo só
  restringe esse conjunto — nunca cria vínculo novo.

## Regras

- A engine **sugere**, o cliente **decide**: renomear, criar subsample manual e
  mover amostra são operações da UI, e uma reextração não reescreve a escolha do
  cliente.
- `DELETE` de subsample nunca apaga: inativa e põe `subsample=None` nas amostras
  (dados e gates intactos).
- `source_path` do subsample é imutável — é o registro do que veio no ZIP; o
  rótulo editável é o `name`.

## Arquivos a tocar

- `fcs_parser/models.py`, `fcs_parser/serializers.py`, `fcs_parser/views.py`,
  `fcs_parser/urls.py`, `fcs_parser/services/process_experiment_file.py`,
  `fcs_parser/migrations/0010_*`, `0011_backfill_source_path.py`,
  `fcs_parser/services/repair_source_path.py`,
  `fcs_parser/management/commands/repair_source_path.py`.
- Parte 2: `analytics/gate_scope.py`, `analytics/serializers.py`,
  `analytics/views.py`.

## Critérios de aceite

- [x] ZIP com `tempo_1/a1.fcs` e `tempo_2/a1.fcs` → duas amostras, dois
      subsamples, nenhuma colisão.
- [x] Arquivo na raiz do ZIP → `subsample=None`.
- [x] `source_path` duplicado no mesmo experimento → rejeitado.
- [x] Reextração usa o caminho exato.
- [x] Criar/renomear/inativar subsample e mover amostra (inclusive `null`) pela
      API, com autorização por experimento.
- [x] Backfill não quebra experimentos antigos.
- [x] `repair_source_path` remapeia nome único no ZIP sem inativar nada, marca
      homônimos como ambíguos e, com `--recreate`, inativa as linhas antigas e
      recria uma amostra por entrada do ZIP; `--dry-run` não grava nada.
- [x] `scope="subsample"` propaga só nas amostras do subsample; amostra sem
      subsample se comporta como `scope="file"` (`applied_scope` no retorno).
- [x] Geometria com `scope="subsample"` mantém a cópia atachada à família;
      `scope="file"` continua desanexando.
- [x] `dry_run` com `scope="subsample"` lista exatamente as amostras afetadas
      e não grava nada.

## Fora de escopo

- Amostra em mais de um subsample (M:N) — ver consequências do ADR-0006.
- Dedução de subsample por metadado do FCS.
- UI: `pandora-front/docs/prd/FE-11-subsamples.md`.
