# ADR-0007 — Nome de gate filho é hierárquico (`P1.1`)

- **Status:** Aceito
- **Data:** 2026-09-05
- **Contexto do código:** front `useGateNaming` / criação de gate; backend `unique_gate_name_per_parent`

## Contexto

A numeração automática era global por tipo (`P1`, `P2`, ...), reiniciando por
nível. Desenhar um polígono dentro de `P1` gerava outro `P1`, e o export passava
a ter duas colunas indistinguíveis — o relatório ficava inútil justamente na
hierarquia profunda, que é o caso normal de imunofenotipagem.

## Decisão

O nome sugerido para um gate filho é derivado do pai: filho de `P1` vira `P1.1`,
`P1.2`; filho de `P1.1` vira `P1.1.1`. Gates já existentes mantêm seus nomes
(não há renomeação retroativa), e o usuário pode sobrescrever a sugestão. No
relatório, a coluna mostra o caminho completo (`Amostra › P1 › P1.1`), então
homônimo legado também fica distinguível.

## Alternativas consideradas

### A) Só mudar a identificação no export, mantendo os nomes iguais

Descartada: resolve o export e deixa a árvore na tela ambígua, que é onde o
usuário trabalha.

### B) Unicidade global de nome por experimento

Descartada: proíbe o caso legítimo — o mesmo nome de população em ramos
diferentes ou em amostras diferentes é esperado (e é o que a linhagem do
ADR-0003 agrupa).

### C) Expor o id do gate na UI

Descartada: id é detalhe de implementação; o usuário raciocina sobre população,
não sobre chave primária. Ids continuam sendo a identidade na comunicação
front↔back, apenas sem aparecer como rótulo.

## Consequências

- Nome carrega a posição na árvore, então mover um gate de pai deixa o nome
  desatualizado (aceito: é sugestão, não invariante).
- Nomes ficam longos em árvores profundas; a UI mostra o caminho com truncagem.
