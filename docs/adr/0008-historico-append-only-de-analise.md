# ADR-0008 — Histórico da análise como log append-only de eventos

- **Status:** Proposto
- **Data:** 2026-09-13
- **Contexto do código:** a implementar; ver `prd/BE-08-historico-rollback.md`

## Contexto

Hoje a estratégia de análise (árvore de gates, geometrias, nomes, cores,
subsamples) só existe no estado atual. Quando várias pessoas do laboratório
editam o mesmo experimento, ninguém sabe quem mudou o quê nem consegue voltar
uma alteração específica; as decisões que já tomamos pioram isso, porque são
destrutivas por natureza (sobrescrever ao aplicar, desanexar no reshape,
propagar no escopo do experimento).

A referência que o time quer é a de um documento colaborativo: ver o autor e a
data de cada edição, revisar as edições recentes e reverter uma delas.

## Decisão (proposta)

Registrar **eventos**, não estados: uma tabela append-only
(`AnalysisRevision`) com alvo (gate/subsample/amostra/experimento), ação,
`payload_before`/`payload_after`, autor e timestamp, gravada nos pontos que já
estão centralizados no backend (criar, remodelar, renomear/recolorir, aplicar,
excluir/desabilitar, mover de subsample).

Sobre essa base:

- **auditoria** = ler o log filtrado por alvo ou por autor;
- **revisão** = diff legível de um evento ("`P1` mudou de geometria", com antes/depois);
- **rollback** = aplicar o inverso de um evento como um **novo** evento (o log
  nunca é reescrito);
- **snapshot/estratégia nomeada** (equivalente ao workspace do FlowJo) é uma
  camada posterior em cima do log, não um pré-requisito.

## Alternativas consideradas

### A) Versionar o experimento inteiro por snapshot a cada alteração

Descartada: cada snapshot copia a árvore toda, cresce rápido e não responde
"quem mudou este gate" sem fazer diff de árvores. Pesado para o ganho.

### B) Django-reversion / biblioteca genérica de auditoria de modelo

Descartada como base: registra mudança por linha de tabela, então a leitura sai
em termos de `GateModel.pk` e colunas, não em termos de "propagou nome no
experimento" — e reverter uma operação em lote seria reverter N linhas sem saber
que elas eram uma ação só. Nossa unidade de revisão é a operação do usuário.

### C) Log de auditoria sem capacidade de reverter

Descartada: resolve metade do pedido. Guardar `payload_before` custa quase nada
no mesmo insert e é o que viabiliza o rollback.

### D) Event sourcing puro (estado derivado do log)

Descartada: obrigaria reconstruir a árvore para cada plot e reescrever todo o
backend. Mantemos o estado atual como verdade operacional e o log como histórico.

## Consequências

- Toda mutação de análise passa a ter um ponto de escrita obrigatório; um
  endpoint que esquecer de registrar cria um buraco silencioso no histórico.
- O log cresce com o uso (linhas pequenas, mas sem expurgo — coerente com
  ADR-0005) e precisa de índice por alvo e por experimento.
- Rollback de uma alteração antiga pode conflitar com alterações posteriores; a
  reversão será oferecida com o mesmo `dry_run`/confirmação do ADR-0002, não
  aplicada às cegas.
- Edição concorrente de duas pessoas no mesmo gate continua "último a escrever
  ganha" — o log torna isso visível, mas não resolve; merge fica fora do escopo.
