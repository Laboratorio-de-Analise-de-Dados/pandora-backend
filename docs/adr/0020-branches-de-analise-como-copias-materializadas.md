# ADR-0020 — Branches de análise como cópias materializadas de gates

- **Status:** Aceito
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
revisões.

**Escopo e propriedade:** branch é uma linha de trabalho nomeada dentro
de **um** experimento — não é presa a usuário (qualquer editor commita
nela) e não é criada automaticamente por editor (linhas paralelas só
existem por ação explícita; dois editores na mesma branch produzem
revisões sequenciais na mesma linha, como hoje). O cenário "dois
experimentos com a mesma aquisição/painel" **não** é caso de branch —
as amostras são `file_data` distintos; portabilidade de estratégia entre
experimentos é domínio do BE-19 (templates/cópia de análise), não deste
ADR. Concretamente:

- `AnalysisBranch(experiment, name, created_by, base_branch,
  fork_snapshot, is_main, active)` — uma por linha de trabalho. Todo
  experimento passa a ter a branch default `main` (migração cria e
  associa gates/revisões existentes). `fork_snapshot` guarda o mapa do
  fork: `{"pairs": {<gate_id_da_branch>: <gate_id_da_base>}, "base":
  {<gate_id_da_base>: campos no fork}}` — é o "merge base" do diff.
- `GateModel.branch` — fork materializa: copiar a árvore de cada amostra
  para a branch nova. A identidade cross-branch usa **`forked_from`**
  (FK nova para o gate de origem), não `copied_from`: edição de
  geometria desfaz `copied_from` por decisão do ADR-0003, então ele não
  sobreviveria como âncora de merge; `forked_from` é linhagem pura.
- `AnalysisRevision.branch` — cada revisão pertence a uma branch
  (`NULL` = ação experiment-wide, presente em todos os recortes); a
  timeline filtrada por `?branch=` é o "log" daquela linha.
- **Merge** = diff estrutural entre as árvores materializadas (não
  replay de revisões): para cada gate da branch filha, casar pelo par
  do `fork_snapshot`/`forked_from`; criado-só-na-filha aplica,
  deletado-só-na-filha aplica, **editado nos dois lados desde o fork →
  conflito** (`f:` edit-vs-edit, `dt:` deletado-na-base, `ds:`
  deletado-na-branch-editado-na-base). Resolução humana por conflito:
  `mine` (fica a base), `theirs` (vale a branch), `both` (mantém a base
  e cria a versão da branch renomeada — só edit-vs-edit). O merge grava
  revisões padrão (create/update/delete) + um marco `action="merge"`
  citando as constituintes — reverter o merge desfaz a cadeia em ordem
  inversa.
- `CompensationMatrix.branch` — **adiado**: a v1 mantém a matriz
  aplicada experiment-wide (isolamento por branch é refinamento quando
  a UI pedir — ver TRACKER).
- `AnalysisResult` e caches são por branch — o resultado pertence ao
  gate da branch (não é compartilhado entre linhas) e trocar de branch
  (`?branch=` nas leituras, default main) nunca invalida nem muta
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
diff de árvores. Mais simples de raciocinar: `forked_from` +
`fork_snapshot` dão identidade estável e merge-base, e o motor de
conflito reusa a mecânica de drift do revert. Custo: fork de
experimento grande duplica N×M linhas de gate — aceitável (são JSONs
pequenos) e explícito ("criar branch" é uma ação cara e auditada).

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
