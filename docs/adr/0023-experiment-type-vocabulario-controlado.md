# ADR-0023 — Tipo de experimento como vocabulário controlado e checkpoint em gates do Juvia

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
  vocabulário controlado** — extensível por **admin ou pelo dono do
  experimento** quando o tipo não existir (padrão autocomplete-cria:
  a UI sugere os existentes primeiro e oferece "Criar X" como escape).
  "Admin" cobre os dois níveis: `is_super_admin` e `org_admin` — o admin
  da organização do experimento cura o vocabulário do próprio lab. Quem
  cria o próprio experimento é dono por definição, então o fluxo normal
  de criação continua sem fricção; membro comum editando experimento
  alheio só escolhe entre tipos existentes. Não é `choices` hardcoded
  nem texto livre.
- **Dedup case-insensitive na origem** — a tabela guarda
  `name_normalized` (lower/trim) com unicidade; "Stem Cell" e "stem
  cell" convergem para a mesma entrada. A escrita pelo `save()` do
  experimento faz `get_or_create` normalizado: quem digita um tipo
  existente reutiliza, quem digita novo cria — respeitando a regra de
  permissão acima, checada no serializer/endpoint.
- `POST /experiment/types/` (endpoint solto, sem experimento no
  contexto) exige **admin** — super admin ou `org_admin` ativo de alguma
  organização; o GET da listagem é aberto a autenticados.
- Curadoria (mesclar/renomear tipos quase-duplicados) fica como função
  administrativa posterior, fora do caminho de criação.
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

- Vocabulário evolui sem deploy e sem fricção pra quem cria o próprio
  experimento; o autocomplete empurra pra reutilizar o que existe e o
  dedup normalizado contém a fragmentação. A restrição a dono/admin limita
  poluição por terceiros: erros de digitação ("stem cel") só entram via
  dono do experimento ou admin — mitigado pela curadoria posterior.
- Um checkpoint extra por gate gerada — volume pequeno, já previsto pelo
  mecanismo.
- Habilita em fases: estatística "qual modelo performa por tipo" →
  default de modelo por `experiment_type` → base para clustering
  ajustado por tipo (juvia ADR-0003).
- O sinal de edição é ruidoso — mistura erro do modelo com preferência
  do analista. Não substitui validação cega contra gate de especialista.
