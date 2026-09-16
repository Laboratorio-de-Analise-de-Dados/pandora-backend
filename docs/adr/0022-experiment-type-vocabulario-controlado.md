# ADR-0022 — Tipo de experimento como vocabulário controlado e checkpoint em gates do Juvia

- **Status:** Proposto
- **Data:** 2026-09-16
- **Contexto do código:** `fcs_parser/models.py` (`ExperimentModel`),
  `analytics/` (gates, checkpoints — ADR-0017), integração futura com o
  serviço juvia (`juvia-project/docs/adr/0001`, `0003`)

## Contexto

O Juvia (microserviço de clustering) vai gerar gates a partir de clusters
— hoje FSC-A × SSC-A, depois derivadas da amostra controle. Duas coisas
precisam existir do lado do Pandora para que isso vire aprendizado e não
só conveniência:

1. **Rotular o experimento por tipo de análise** (ex.: stem cell, PBMC,
   beads/CBA, tripanossomatídeos) para permitir comparar "modelo × tipo"
   ao longo do tempo. Texto livre fragmenta labels ("stem cell" ×
   "Stem Cell" × "SC") e inviabiliza estatística por tipo.
2. **Preservar o gate como o Juvia gerou** antes das edições do
   especialista. O delta gerado × aceito é o sinal de supervisão da
   pesquisa — sem um marco no ponto da criação, o histórico mistura
   "proposto pelo modelo" com "criado/editado pelo usuário".

## Decisão (proposta)

- `ExperimentModel` ganha `experiment_type` como FK para uma **tabela de
  vocabulário controlado** — extensível por admin, com opção "outro" +
  descrição livre como escape. Não é `choices` hardcoded nem texto livre.
- Toda gate criada a partir de resultado do Juvia gera **checkpoint
  imediato** (mecanismo do ADR-0017) com metadado de origem:
  `source=juvia`, modelo e hiperparâmetros usados.
- O Juvia permanece stateless — `experiment_type` não muda a computação
  dele; no futuro pode viajar no request para o Pandora escolher
  modelo/params default daquele tipo.

## Alternativas consideradas

### A) `experiment_type` como texto livre ou enum

Texto livre fragmenta o label space e corrompe o dataset na origem. Enum
hardcoded congela o vocabulário — tipos do laboratório (tripanossomatídeos)
não cabem em lista fixa e tipo novo exigiria deploy.

### B) Depender do histórico append-only (ADR-0008) sem checkpoint nomeado

O evento de criação já existe no log, mas sem marcador de origem não dá
para separar "proposto pelo modelo" de "criado pelo usuário", nem
restaurar/consultar o ponto exato da proposta.

### C) Guardar o sinal de supervisão no Juvia

Inviável — o Juvia é stateless por decisão (juvia ADR-0001). Quem detém o
estado da análise é o Pandora.

## Consequências

- Vocabulário evolui sem deploy; exige curadoria (quem adiciona tipos e
  como deduplicar).
- Um checkpoint extra por gate gerada — volume pequeno, já previsto pelo
  mecanismo.
- Habilita em fases: estatística "qual modelo performa por tipo" →
  default de modelo por `experiment_type` → base para clustering
  ajustado por tipo (juvia ADR-0003).
- O sinal de edição é ruidoso — mistura erro do modelo com preferência
  do analista. Não substitui validação cega contra gate de especialista.
