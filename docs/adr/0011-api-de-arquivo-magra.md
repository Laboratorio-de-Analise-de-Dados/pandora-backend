# ADR-0011 — API de arquivo magra: headers crus e lote orquestrado no cliente

- **Status:** Aceito
- **Data:** 2026-09-13
- **Contexto do código:** `fcs_parser/views.py` (`FileHeadersView`,
  `FileSubsampleView`), `fcs_parser/serializers.py`
  (`ListFileDataSerializer`), `fcs_parser/models.py` (`FileDataModel.headers`)

## Contexto

Duas demandas do front chegaram juntas sobre a API de amostras:

1. Exibir metadados do header FCS (data de aquisição, equipamento, total de
   eventos). `FileDataModel.headers` já guarda o dict completo vindo do
   `readfcs.view()` — faltava só expor.
2. Mover várias amostras para um subsample de uma vez. A API só tinha
   `PATCH /experiment/file/<id>/subsample` — um arquivo por chamada.

A questão arquitetural era a mesma nos dois casos: engordar a API (serializer
curado, endpoint de lote) ou mantê-la magra e deixar o cliente orquestrar.

## Decisão

A API de arquivo permanece magra. Na prática:

- `GET /experiment/file/<id>/headers` devolve `headers` **verbatim** — sem
  whitelist de keywords e sem serializer de reshape. O FCS varia por
  citômetro/versão; o front decide quais campos ganham label amigável e mostra
  o resto na lista bruta, então campo novo aparece na UI sem deploy de backend.
- Não existe endpoint de lote para mover amostras: o front dispara N
  `PATCH .../subsample` em paralelo (`Promise.allSettled`) e faz um único
  refetch. Cada chamada reusa a autorização por experimento já existente.

## Alternativas consideradas

### A) Serializer curado de headers (whitelist `$date`, `$cyt`, `tot`, ...)

Descartada. Os keywords FCS variam por equipamento e versão do padrão; uma
whitelist apodrece e esconderia campos que o usuário pode querer inspecionar.
O "ver todos os campos" do front precisa do dict inteiro de qualquer forma.

### B) Endpoint de lote (`PATCH /experiment/files/subsample {ids: [], subsample}`)

Descartada por ora. O lote típico é dezenas de amostras; N chamadas
paralelas são aceitáveis e falha parcial é um estado legítimo (reporta a
contagem). Um endpoint bulk adicionaria serializer, validação de conjunto e
semântica de atomicidade para um ganho marginal hoje.

### C) Headers embutidos no `ListFileDataSerializer`

Descartada. O dict de headers é grande por arquivo e a listagem traz muitos —
inflaria o payload de toda a tela de experimento para um dado que o usuário só
vê ao abrir o diálogo de uma amostra.

## Consequências

- O front assume a orquestração do lote: `Promise.allSettled`, toast de falha
  parcial e invalidação única — ver `useExperimentPageActions` e FE-14 no
  pandora-front.
- Mover centenas de amostras gera centenas de requests. **Dívida registrada:**
  se o volume de amostras por experimento crescer ou a movimentação exigir
  atomicidade, criar o endpoint de lote (rever alternativa B).
- Headers nunca ficam desatualizados com o armazenado — o contrato é o próprio
  JSONField; mudança de keywords do `readfcs` flui até a UI sem tocar o backend.
