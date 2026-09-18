# BE-26 — Identificação de amostras controle pelo usuário

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/control-marking`
**Status:** não iniciado — estende ADR-0019. Revisado 2026-09: marcação
passa a ser **por amostra** (arquivo), não só por subsample, e a listagem
ganha pistas de contexto (poço/placa, sugestão por heurística).

## Problema

A frente "clusterizar o controle → derivar gates" (juvia ADR-0003) exige
saber qual amostra é o controle. Não existe sinal determinístico no FCS
— nome de arquivo e keywords de header são convenções falíveis. A
informação tem que vir do usuário, mas pedir no upload bloqueia um fluxo
bulk e o usuário pode ainda não saber qual análise vai rodar.

O mecanismo atual (ADR-0019) marca **subsamples** — suficiente para
tubos de compensação, mas falha em experimento de placa: caso real
(`cba.zip`) traz 64 arquivos soltos na raiz (`Specimen_001_A1_A01.fcs`),
sem diretório → nenhum subsample é criado → nada é marcável sem
agrupamento manual prévio. A marcação precisa existir no nível da
amostra.

## Escopo

### 1. Marcação por amostra (FileDataModel)

- `control_type`/`control_channel` em `FileDataModel`, espelhando o
  modelo do subsample (ADR-0019). PATCH a qualquer momento — **não
  bloqueia upload**
- Vocabulário por arquivo: `unstained`, `single_stain`, `fmo`,
  `isotype`, `beads`, `biological`. Cobre os tipos que a derivação de
  gates precisa distinguir — gate de positividade usa FMO/isotípico/
  unstained; usar o tipo errado é erro silencioso. `beads` separa
  controle de compensação em bead (CBA) de controle em célula
  (`single_stain`)
- Canal obrigatório e fluorescente (mesma validação de
  `fluorescent_channels` do `SubsampleSerializer`) para `single_stain`,
  `fmo`, `isotype`, `beads`; vazio para `unstained` e `biological`
- A marcação por **subsample** do BE-22 não muda: é o que o cálculo de
  compensação consome (réplicas agrupadas). A marcação por arquivo
  alimenta a derivação de gates; unificar os dois consumidores é
  evolução posterior, não deste MR

### 2. PATCH em lote

`PATCH /experiment/<id>/controls/` recebe
`{marks: [{file_id, control_type, control_channel}]}` e aplica numa
transação — a UI de placa tagueia dezenas de poços de uma vez.
Validação por serializer; erro em qualquer item devolve 400 com erros
por `file_id` e **nada é aplicado** (atômico — repaint parcial deixa a
placa inconsistente).

### 3. Pistas de contexto na listagem

`GET /experiment/list/data/<id>/` passa a trazer por amostra:

- `well_id` — poço (`A1`…`H12`) lido do header FCS (`WELL ID`/`TUBE
  NAME`; fallback: regex `_?[A-H](0?[1-9]|1[0-2])\b` no `file_name`) —
  é o que posiciona a amostra no mapa de placa
- `plate` — `PLATE NAME`/`PLATE ID` do header quando presente; é o
  sinal "experimento de placa" para o front escolher a grade
- `suggested_control` — `{control_type, control_channel|null}` por
  heurística de filename/keywords (`unstained`, `fmo`, `neg`,
  `control`, `comp`, `beads`, fluoróforos `fitc`/`pe`/`apc`...).
  Informativo: **nunca é aplicado sozinho** — o front pré-marca e o
  usuário confirma

### 4. Heurística e parse de poço

Funções puras em `fcs_parser/services/` (testáveis, sem IO): filename +
keywords do header → `suggested_control`; header/`file_name` →
`well_id`. Keywords do header já são extraídas no parse (`FileHeaders`
) — o service consome o que já está materializado, sem reparsear FCS.

## Arquivos a tocar

- `fcs_parser/models.py` — `control_type`/`control_channel` em
  `FileDataModel` (+ migration, expand-contract: campos novos, nada
  dropado)
- `fcs_parser/serializers.py` — `ListFileDataSerializer` (+`well_id`,
  `plate`, `suggested_control`, `control_type`/`control_channel`),
  serializer do PATCH em lote
- `fcs_parser/views.py` / `urls.py` — `PATCH <id>/controls/`
- `fcs_parser/services/` — heurística de sugestão + parse de poço

## Critérios de aceite

- [ ] Usuário marca/desmarca controle (com tipo) por amostra, fora do
      fluxo de upload
- [ ] PATCH em lote atômico: erro em um item não aplica nenhum
- [ ] Listagem traz `well_id`/`plate`/`suggested_control` por amostra —
      informativos
- [ ] Canal exigido e validado contra os canais fluorescentes do
      experimento para `single_stain`/`fmo`/`isotype`/`beads`
- [ ] Marcação de subsample (compensação, BE-22) inalterada

## Fora de escopo

- Detecção estatística automática de controles (ML sugerindo) — fase
  posterior quando houver dados
- O consumo do controle pelo Juvia para derivar gates — juvia JV-03
- UI do fluxo de identificação — `pandora-front` FE-34 (lista) e FE-35
  (mapa de placa)
- Unificar compensação (subsample) e marcação por arquivo num só
  consumidor
