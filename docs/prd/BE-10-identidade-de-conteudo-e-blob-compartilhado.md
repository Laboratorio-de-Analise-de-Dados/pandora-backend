# BE-10 — Identidade de conteúdo (`content_guid`, `sha256`) e blob compartilhado

**Repo:** pandora-backend · **Tipo:** feature/refactor · **Base:** `main`
**Status:** Implementado na branch `feat/file-identity-copy` — freeze/delete
segue fora de escopo.
Depende do [BE-09](BE-09-headers-fcs.md) (headers expostos), conversa com
[BE-08](BE-08-historico-rollback.md) (histórico/rollback) e destrava
[BE-11](BE-11-copiar-mover-experimento.md) e [BE-12](BE-12-dedup-no-upload.md).

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

### 1. Identidade em três colunas (cada uma no nível certo)

O `guid` e o hash de conteúdo são do `.fcs` individual; o hash do blob é do
upload inteiro (ZIP ou `.fcs` solto):

- `FileDataModel.content_guid` — keyword `guid` do header FCS, promovido a
  coluna no parse. Identidade lógica **da amostra**, âncora de referências
  de análise (gates propagados, histórico/rollback do BE-08).
  `UniqueConstraint(experiment, content_guid)` — medido: zero duplicados
  internos na base atual; quem não grava `guid` fica `null` e cai no
  `source_path` (que vira só dica de agrupamento pro subsample inicial, não
  mais identidade — ajustar ADR-0006 em ADR novo).
- `FileDataModel.content_sha256` — hash do `.fcs` individual, calculado na
  extração. É a unidade real da dedup: detecta a mesma amostra em ZIPs ou
  uploads diferentes (o hash do ZIP quase nunca colide).
- `FileModel.sha256` — hash do blob no upload. Integridade do freezer e
  dedup de upload idêntico. `guid`+metadados (`cyt`/`date`/`inst`/`tot`) é
  a camada metodizável de confirmação, o hash decide bytes iguais.

### 2. `FileModel` = referência experimento↔blob compartilhado

Implementação optou por manter `FileModel.experiment` como `OneToOne` (um
upload por experimento) e compartilhar o blob no nível do **caminho**:
copiar ou reutilizar cria outra linha de `FileModel` apontando pro mesmo
`file.name`, sem duplicar bytes e sem quebrar `file_model.experiment` nos
serviços. A identidade física é o `sha256` — duas linhas com o mesmo hash
são o mesmo blob. Copiar/mover experimento
([BE-11](BE-11-copiar-mover-experimento.md)) cria linhas novas de
`FileDataModel`/`Subsample`/`Gate` sobre o mesmo blob — análise
independente. Upload com `sha256` já existente reutiliza o blob
([BE-12](BE-12-dedup-no-upload.md)).

### 3. Download = artefato derivado, não o blob guardado

A unidade gerenciada é o `.fcs` (via `content_guid`/`content_sha256`) — o
mesmo arquivo pode existir em experimentos diferentes com organizações de
subsample diferentes. O ZIP de download do experimento é **reconstruído na
hora** com a estrutura de pastas dos subsamples atuais do usuário; o blob
armazenado segue sendo só o freezer/fonte da verdade dos bytes.

### 4. Política de freeze/delete (futura — não faz parte desta entrega)

Por ora **experimento desativado não apaga nada**: os arquivos ficam. A
política futura joga a responsabilidade no usuário, com garantia de entrega
antes de qualquer deleção:

- Gatilho: experimento cujo **último responsável foi desativado** (ou marcado
  pelo dono) e sem acesso por N dias (ex.: 7 — `last_accessed` mede isso).
- Rotina envia por e-mail os dados de citometria ao responsável (ZIP ou links
  assinados) — "seus dados, sua guarda".
- Só depois de **garantir que o usuário recebeu** (confirmação de entrega ou
  download) e a janela passar sem acesso → exclui experimento e arquivos.
- Blob compartilhado por outra cópia/experimento nunca entra na limpeza —
  deleção física só de `FileModel` órfão.
- Parquet frio pode expirar por TTL antes disso sem risco (é cache
  regenerável do blob — ADR-0004).

Nunca matar experimento alheio sem o e-mail entregue — a confirmação de
recebimento é o portão da deleção.

## Arquivos tocados

- `fcs_parser/models.py` + migrações `0012`/`0013` — `FileModel.sha256`,
  `FileDataModel.content_guid`/`content_sha256`,
  `UniqueConstraint(experiment, content_guid)`, backfills
- `fcs_parser/services/process_experiment_file.py` — `file_sha256()`,
  `content_guid`/`content_sha256` populados nos três pontos de extração
- `fcs_parser/services/copy_experiment.py` — clone linha-a-linha sobre o
  mesmo blob
- `fcs_parser/views.py` + `urls.py` — `ExperimentCopyView`,
  `FileHashCheckView`, `complete/` com `reuse`, move via
  `PATCH organization_id`
- Pendente: endpoint de download reconstruindo ZIP por subsample; rotina de
  retenção (futura)

## Critérios de aceite

- [x] Copiar experimento cria análise independente sem re-upload; os bytes do
      storage não crescem.
- [x] Mesmo `guid` duas vezes no mesmo experimento é rejeitado pela
      constraint.
- [x] Arquivo sem `guid` continua funcionando (coluna `null`, sem
      constraint).
- [ ] Download do experimento reconstruído com a organização de subsamples
      atual (endpoint a implementar).
- [ ] Rotina de dedup/limpeza nunca remove arquivo referenciado por
      experimento ativo (futura).

## Fora de escopo

- Merge de análises entre experimentos, compartilhamento de gates entre
  experimentos independentes.
- UI do front para a cópia (PRD próprio no pandora-front quando a API existir).
