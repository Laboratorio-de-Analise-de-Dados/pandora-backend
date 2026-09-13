# BE-10 — Identidade de conteúdo (`content_guid`, `sha256`) e blob compartilhado

**Repo:** pandora-backend · **Tipo:** feature/refactor · **Base:** `chore/ai-setup`
**Status:** Proposto — direção discutida, escopo a fechar antes de codar.
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

### 1. Identidade em duas colunas novas (cada uma no nível certo)

O `guid` é do `.fcs` individual; o hash é do blob upado (ZIP ou `.fcs` solto):

- `FileDataModel.content_guid` — keyword `guid` do header FCS, promovido a
  coluna no parse. Identidade de conteúdo **da amostra**, âncora de
  referências de análise (gates propagados, histórico/rollback do BE-08).
  `UniqueConstraint(experiment, content_guid)` — medido: zero duplicados
  internos na base atual; quem não grava `guid` fica `null` e cai no
  `source_path` (que vira só dica de agrupamento pro subsample inicial, não
  mais identidade — ajustar ADR-0006 em ADR novo).
- `FileModel.sha256` — hash do blob no upload. Chave definitiva da dedup de
  storage: `guid`+metadados (`cyt`/`date`/`inst`/`tot`) é a camada
  metodizável de confirmação, o hash decide bytes iguais.

### 2. `FileModel` vira o blob compartilhado (quase-`StoredFile`)

Hoje `FileModel.experiment` é `OneToOne` — blob filho único. O desenho é
tirar essa posse: `FileModel` vira "arquivo guardado" puro (path, sha256,
tamanho) e quem liga experimento↔blob é o `FileDataModel` (já tem as duas
FKs). Copiar/mover experimento ([BE-11](BE-11-copiar-mover-experimento.md)) =
linhas novas de `FileDataModel`/`Subsample`/`Gate` apontando pro **mesmo**
`FileModel` — nenhum byte duplicado, análise independente. Upload com
`sha256` já existente reutiliza o blob ([BE-12](BE-12-dedup-no-upload.md)).

### 3. Política de freeze/delete (futura — não faz parte desta entrega)

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

## Arquivos a tocar (quando implementar)

- `fcs_parser/models.py` — `FileModel.experiment` sai de `OneToOne`
  (ou a FK migra pro uso via `FileDataModel`); colunas `sha256` e
  `content_guid`; `UniqueConstraint(experiment, content_guid)`
- `fcs_parser/services/process_fcs.py` — extrai `guid` e `sha256` no parse
- `fcs_parser/views.py` + `urls.py` — `ExperimentCopyView`; `init/` avisando
  hash já existente
- `fcs_parser/services/` — rotina de retenção (management command ou Celery)
- `docs/adr/` — ADR novo marcando a mudança de identidade (mexe com ADR-0006)

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
