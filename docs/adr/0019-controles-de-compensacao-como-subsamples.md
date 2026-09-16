# ADR-0019 — Controles de compensação como subsamples marcados

- **Status:** Aceito
- **Data:** 2026-09-15
- **Contexto do código:** `fcs_parser/models.py` (SubsampleModel —
  agrupamento por diretório do ZIP, `name` editável, soft delete),
  `fcs_parser/views.py` (SubsampleListCreateView, FileSubsampleView)

## Contexto

Calcular uma matriz de spillover exige **controles**: um controle
negativo (unstained) e um single-stain por canal fluorescente. Na prática
de laboratório esses tubos já chegam no ZIP como uma pasta separada
(`controles/FITC.fcs`, `controles/unstained.fcs`...) — que o BE-07 já
materializa como subsamples.

A pergunta era onde vive a informação "este arquivo é o controle do canal
X": criar uma estrutura paralela de controles, ou reutilizar o
agrupamento que já existe.

## Decisão

O **subsample ganha um papel de controle opcional**: `control_type`
(`null` — amostra normal | `"unstained"` | `"single_stain"`) e, quando
single-stain, `control_channel` (o canal que ele cora, ex.: `"FITC-A"`).
Todos os arquivos dentro do subsample-controle são réplicas daquele
controle — a estatística de cálculo faz pool sobre eles.

O endpoint de cálculo **deriva o mapeamento canal→controle dos subsamples
marcados** (um single_stain por canal + o unstained), aceitando override
explícito no payload quando o usuário quiser apontar amostras soltas.
Sugestão automática por nome de arquivo ("FITC", "unstained") pode vir
como *sugestão* na resposta de leitura — nunca como verdade silenciosa.

Subsample-controle continua sendo subsample: aparece na árvore, aceita
renomear/mover amostras, soft delete inativa o controle junto. Marcar um
subsample como controle **não** o exclui da análise (pode-se gatear
controles normalmente — é até desejável para inspeção).

## Alternativas consideradas

### A) Flag em `FileDataModel` (controle por amostra)

Descartada como modelo primário: dispersa a informação (cada réplica
precisaria ser marcada uma a uma) e duplica o conceito de agrupamento que
o subsample já entrega de graça. O override por arquivo no payload de
cálculo cobre o caso granular sem poluir o modelo.

### B) Entidade `ControlSample` separada

Descartada: recriaria nome, vínculo com experimento e soft delete que o
SubsampleModel já tem; dois conceitos de "grupo de amostras" para o front
apresentar.

### C) Inferir controle pelo nome do arquivo

Descartada como verdade: nomes variam por laboratório ("comp-beads",
"FITC only", "neg"). Vale como heurística de *sugestão* na UI, com
confirmação humana — nunca como classificação automática.

## Consequências

- Facilita: UX natural (a pasta "controles" do ZIP já vira o conjunto de
  controles); réplicas agregadas sem esforço; nada de modelo paralelo.
- Dívida: validar no cálculo que `control_channel` corresponde a um canal
  fluorescente real do experimento (não FSC/SSC/Time) e que não há dois
  single-stain para o mesmo canal — erro de domínio deve virar 400
  nomeando o conflito.
- Limite assumido: um unstained por experimento na v1 (múltiplos
  negativos por autofluorescência de tecido ficam para evolução).
