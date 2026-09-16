# ADR-0020 — Branches de análise como cópias materializadas de gates

- **Status:** Proposto
- **Data:** 2026-09-16
- **Contexto do código:** `analytics/models.py` (`GateModel`,
  `AnalysisRevision`, `AnalysisCheckpoint`, `CompensationMatrix`),
  `analytics/history.py` (revert/restore com detecção de drift),
  `fcs_parser/services/compensation.py` (matriz aplicada por experimento)

## Contexto

A análise é single-head: toda revisão escreve na mesma linha do
experimento. Dois analistas editando gates do mesmo experimento se
atropelam — a timeline (BE-08/BE-20) registra quem fez o quê, mas não
isola propostas nem permite convergir depois. O PRD BE-23 registra a
visão "engine estilo git"; a decisão em aberto é **o que é o estado
visível de uma branch**.

## Decisão

Branch = **cópia materializada das árvores de gates**, não linhagem de
revisões. Concretamente:

- `AnalysisBranch(experiment, name, created_by, base_branch,
  forked_at, head_revision)` — uma por linha de trabalho. Todo
  experimento passa a ter a branch default `main` (migração cria e
  associa gates/revisões/matrizes existentes).
- `GateModel.branch` — fork materializa: copiar a árvore de cada amostra
  para a branch nova, com `copied_from` apontando para o gate de origem
  (o campo já existe — identidade entre branches sai **de graça** pela
  cadeia de `copied_from` até o ancestral comum).
- `AnalysisRevision.branch` — cada revisão pertence a uma branch; a
  timeline filtrada por branch é o "log" daquela linha.
- `CompensationMatrix.branch` — matriz aplicada é por branch (um
  analista pode propor compensação diferente sem contaminar a main).
- Leitura compensada/density/stats ganham `?branch=<id>` (default main);
  a chave de cache passa a incluir branch + matriz.
- **Merge** = diff estrutural entre as árvores materializadas (não
  replay de revisões): para cada gate da branch filha, casar pelo
  ancestral `copied_from`; criado-só-na-filha aplica, deletado-só-na-
  filha aplica, **editado nos dois lados desde o fork → conflito**
  (mesma mecânica de drift que `plan_revert` já devolve). Resolução
  humana por conflito: manter meu / manter dele / manter ambos (rename).
  O merge grava revisões `action="merge"` na branch alvo — auditável e
  revertível como qualquer outra operação.
- `AnalysisResult` e caches são por branch — trocar de branch recalcula
  (density cache por `(file, gate, branch, matrix)`), nada de invalidar
  resultado alheio.

## Alternativas consideradas

### A) Branch como linhagem de revisões (git "de verdade")

`AnalysisRevision.branch` + estado visível = replay da base até o head.
Descartada: os gates são linhas reais compartilhadas — replay por
leitura é caro, e operações como apply entre amostras copiam geometria
entre linhas; reconstruir isso por replay em cada density/stats
invializa. Git guarda blobs imutáveis; nós guardamos linhas mutáveis —
a analogia quebra no storage.

### B) Estado visível = snapshot materializado por branch (escolhida)

Fork copia as árvores; cada branch é um workspace isolado e o merge é
diff de árvores. Mais simples de raciocinar, reusa `copied_from` para
identidade e o motor de conflito do revert. Custo: fork de experimento
grande duplica N×M linhas de gate — aceitável (são JSONs pequenos) e
explícito ("criar branch" é uma ação cara e auditada).

### C) Lock de edição / presença (OT/CRDT)

Um editor por vez por experimento. Descartada: resolve contenção, não
proposta paralela — dois analistas não podem divergir e convergir, que é
o caso de uso do orientador.

## Consequências

- Ganha-se: propostas isoladas por pessoa, merge auditável
  (`action="merge"` entra na timeline e é revertível), blame por branch,
  e o caminho para um analisador não-humano (Juvia) abrir "PRs" de
  análise como autor de branch — `revision.user` já é nullable.
- Dívida assumida: gates deletados na main não propagam para branches
  abertas (fork congela) — merge reporta divergência estrutural, não
  sincroniza silenciosamente.
- A migrar: todo gate/revisão/matriz existente ganha `branch=main` via
  migração de dados — retrocompatível, sem mudança de contrato para o
  front (`?branch=` opcional, default main).
- Conflito de geometria de gate é humano por decisão (fora de escopo
  mergear polígono automaticamente — BE-23 já registra).
