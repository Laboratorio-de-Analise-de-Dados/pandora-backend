# BE-10 — Cópia de experimento sem re-upload e dedup de storage por `guid`

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `chore/ai-setup`
**Status:** Proposto — direção discutida, escopo a fechar antes de codar.
Depende do [BE-09](BE-09-headers-fcs.md) (headers expostos) e conversa com
[BE-08](BE-08-historico-rollback.md) (histórico/rollback).

## Problema

Hoje o arquivo físico é acoplado ao experimento: `FileModel` é `OneToOne` com
`ExperimentModel` e `zip_path` aponta para o blob daquele upload. Consequências
observadas:

- Reaproveitar um experimento (mesmas amostras, outra organização ou espaço
  pessoal) exige subir o ZIP de novo — bytes duplicados no storage.
- A base de dev já mostra o custo: 64 `guid`s duplicados, todos entre
  experimentos diferentes — o mesmo `.fcs` upado várias vezes.
- Sem identidade de conteúdo, o histórico/rollback (BE-08) só consegue mirar
  o `file_data_id` — que morre junto com o experimento.

## Escopo

### 1. `guid` como identidade de conteúdo

- `headers.guid` passa a ser a chave de conteúdo do arquivo **dentro do
  experimento** (medido: zero duplicados internos na base atual).
- Validação no processamento: dois arquivos com o mesmo `guid` no mesmo
  experimento → tratar como duplicata (rejeitar ou marcar, definir com BE-01).
- Fallback para arquivos sem `guid`: `source_path` (ADR-0006) — o guid
  complementa, não substitui.
- Referências de análise (gates propagados, histórico, rollback) passam a
  poder mirar `guid` — sobrevive a rename/movimentação no ZIP e à cópia.

### 2. Copiar experimento para pessoal ou outra organização

```
POST /experiment/<id>/copy
Body: { "title"?, "organization_id": <id> | null }   # null = espaço pessoal

201 { "experiment_id": <novo_id> }
403 sem permissão no experimento origem ou na org destino
```

- Cria experimento novo com **novas** linhas de `FileDataModel`, `Subsample`
  e `Gate` (estado inicial copiado) — a cópia é independente: editar um não
  toca o outro.
- O blob físico não é duplicado: cada `FileDataModel` da cópia referencia o
  mesmo arquivo físico do original, localizado via `guid` (ou `source_path`).

### 3. Rotina de dedup de storage

- Cruzar `guid` + `cyt` + `date` + `inst` (+ `tot`/tamanho como sanidade):
  todos iguais = mesmo conteúdo → uma cópia física, N referências lógicas.
- Só consolida/limpa o que não é referenciado por experimento ativo — nunca
  apaga arquivo em uso (mesma regra do ADR-0005: nada é deletado de verdade).
- **Nota honesta:** `guid` é keyword escrita pelo software de aquisição — não
  é hash de integridade e pode colidir em cenários fora da base medida
  (export re-escrito, software que gera valor constante). Para dedup de
  *bytes* a chave definitiva é `sha256` do arquivo, calculado no upload e
  barato de adicionar; `guid`+metadados serve como chave lógica barata e o
  hash resolve ambiguidade. Decidir na implementação se o `sha256` entra junto.

## Arquivos a tocar (quando implementar)

- `fcs_parser/models.py` — referência de blob físico desacoplada de
  `FileDataModel` (ou coluna `content_ref`/`sha256` nova)
- `fcs_parser/services/process_fcs.py` — extrair `guid` (e hash) no parse
- `fcs_parser/views.py` + `urls.py` — `ExperimentCopyView`
- `fcs_parser/services/` — rotina de dedup (management command ou Celery)
- `docs/adr/` — se a identidade de conteúdo virar constraint de modelo, ADR
  novo (mexe com ADR-0006)

## Critérios de aceite

- [ ] Copiar experimento cria análise independente sem re-upload; os bytes do
      storage não crescem.
- [ ] Mesmo `guid` duas vezes no mesmo experimento é detectado no upload.
- [ ] Arquivo sem `guid` continua funcionando (fallback `source_path`).
- [ ] Rotina de dedup nunca remove arquivo referenciado por experimento ativo.

## Fora de escopo

- Merge de análises entre experimentos, compartilhamento de gates entre
  experimentos independentes.
- UI do front para a cópia (PRD próprio no pandora-front quando a API existir).
