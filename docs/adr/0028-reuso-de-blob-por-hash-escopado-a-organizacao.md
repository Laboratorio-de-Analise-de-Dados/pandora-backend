# ADR-0028 — Reuso de blob por `sha256` escopado à organização, com confirmação de amostra

- **Status:** Proposto
- **Data:** 2026-09-17
- **Contexto do código:** `fcs_parser/views.py` (`_get_or_create_upload`,
  `FileHashCheckView`, `files/complete`), `fcs_parser/models.py`
  (`FileModel`, `FileDataModel.file`/`content_sha256`/`content_guid`),
  `fcs_parser/services/process_experiment_file.py`,
  `fcs_parser/services/copy_experiment.py`, rotina de retenção (BE-10 §4)

## Contexto

Todo upload grava bytes novos em disco mesmo quando o conteúdo já existe:
`_get_or_create_upload` calcula `sha256` mas não o usa para reuso. BE-12
entregou dedup **por amostra dentro do experimento** (skip por
`content_guid`/`content_sha256` reportado em `skipped`) e descartou
explicitamente o reuso de blob cross-experiment — hoje só a cópia
explícita (BE-11) compartilha bytes.

Custo observado na base de dev: dos 64 `guid`s duplicados, todos são o
mesmo `.fcs` re-upado em experimentos diferentes — cada re-upload duplica
o ZIP inteiro no storage. A unidade real de duplicação é o `.fcs` (ZIPs
quase nunca são byte-idênticos: ordem, timestamps e subconjuntos de
amostras variam), e a identidade já existe: `content_sha256` por amostra,
`sha256` por blob.

Limite de segurança conhecido (Harnik et al., IEEE S&P 2010; padrão
CellEngine/Cytobank): dedup com resposta ao cliente fora do escopo de
acesso do usuário vira oráculo de existência e canal de timing. Reuso por
referência é seguro dentro do boundary que o usuário já enxerga.

## Decisão

Reuso de bytes **escopado à organização** (experimento pessoal conta como
org do próprio usuário), em dois níveis:

1. **Blob inteiro — silencioso.** No `complete`, se `FileModel.sha256`
   colide com um blob do pool da org, o novo `FileModel` aponta para o
   `file` já em disco e os bytes recém-montados são descartados — mesmo
   padrão do `copy_experiment`. Sem resposta diferente ao cliente: o
   upload completa igual (não cria oráculo nem canal de timing além do
   já existente via `check-hash` escopado).
2. **Amostra (.fcs) — com confirmação do usuário.** Na extração, amostra
   cujo `content_sha256` (ou `content_guid`) já existe em experimento
   visível **na mesma org** vira proposta de reuso, não fato: a resposta
   reporta `added`/`reused`/`skipped` e o front confirma "é o mesmo
   arquivo" antes de vincular. Confirmado: o `FileDataModel` aponta para
   o `FileModel` que já contém aquele `.fcs` (`fd.file` + `source_path`
   já resolvem por amostra — nenhuma migração de bytes). Negado: segue o
   upload normal, como hoje.

Ciclo de vida: deleção física de blob só quando a contagem de referências
(`FileModel` apontando para o `file` + `FileDataModel.file`) chega a zero
— invariante que a rotina de retenção (BE-10 §4) passa a depender.

## Alternativas consideradas

### A) Pool global na instância

Descartado. Maximiza economia mas cria oráculo de existência cross-tenant
("existe um arquivo com este hash?") — e em citometria o arquivo pode
carregar PHI nos headers. Org é o boundary natural de confiança do
produto.

### B) Dedup só de blob inteiro

Descartado como mecanismo único (mantido como fast-path): ZIPs quase
nunca colidem byte-a-byte; a duplicação real é por `.fcs` individual.

### C) Reuso automático sem confirmação

Descartado. Match de hash vira proposta, não vínculo silencioso — o
usuário decide "é o mesmo arquivo". Protege contra falso positivo de
`guid` mal gerado por vendor e mantém o upload previsível ("subiu o que
eu mandei").

### D) Compartilhar `FileDataModel` entre experimentos (amostra N:N)

Descartado. O que é compartilhável é o **blob** (`FileModel`) — e isso já
acontece sem mudar o modelo: `fd.file` aponta para o `FileModel` que
contém os bytes, qualquer que seja o experimento dono do upload. A linha
`FileDataModel` é a *amostra lógica* e precisa ser por experimento:
`subsample` (a organização em pastas pode divergir entre experimentos),
`active`/`deactivated_by`, `parquet_path` e as referências de análise
(gates, histórico) são estado por-experimento — compartilhar a linha
faria uma edição em A vazar para B. A linha também é metadado barato: o
peso real está no ZIP (dedupado aqui) e no Parquet, que pode virar cache
por `content_sha256` como refinamento futuro sem tocar o modelo.

## Consequências

- Upload de conteúdo repetido deixa de multiplicar storage; o vínculo é
  operação de linha, não de bytes.
- A resposta do upload passa a distinguir `added`/`reused`/`skipped` —
  contrato novo para o front (confirmação de reuso).
- `check-hash` e qualquer consulta de existência seguem escopados —
  nunca pool global visível.
- Deleção física exige refcount correto: apagar blob ainda referenciado
  corrompe experimento alheio. A rotina de retenção vira pré-requisito
  forte, não dívida opcional.
- Blob órfão entre `FileModel` criado e extração continua coberto pelo
  ciclo de retomada (BE-31/ADR-0027); com blob compartilhado, órfão sem
  referência é o único candidato seguro a remoção.
- Dívida assumida: amostras confirmadas como "o mesmo arquivo" mas com
  nomes/pastas diferentes ficam com `source_path` do blob de origem — a
  organização por subsample do experimento novo é por cópia lógica, não
  pelo caminho no ZIP alheio.
