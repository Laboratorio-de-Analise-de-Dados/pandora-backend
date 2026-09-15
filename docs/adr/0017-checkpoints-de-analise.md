# ADR-0017 — Checkpoints de análise como marcos nomeados sobre o log append-only

- **Status:** Proposto
- **Data:** 2026-09-14
- **Contexto do código:** `analytics/models.py` (`AnalysisRevision`),
  `analytics/history.py` (registro + `plan_revert`/`_apply_plan`),
  endpoints `GET /analytics/experiment/<id>/history/` e
  `POST /analytics/experiment/<id>/history/<rev>/revert/` (BE-08);
  ver `prd/BE-20-checkpoints-de-analise.md`

## Contexto

O ADR-0008 entregou o log append-only e o revert por revisão. O uso real do
laboratório é por **sessão de trabalho**: a pessoa abre o experimento, faz uma
sequência de edições (renomeia gates, aplica em amostras, ajusta geometrias) e
quer um ponto de retorno nomeado — "passei os gates de CD4", "antes do
reprocessamento" — ao qual possa voltar inteiro, não revisão a revisão.

A referência mental era o salvamento de dashboard do Grafana, mas com uma
diferença de modelo: no Pandora o resultado da análise é computado no servidor
(stats, density), então adiar a escrita para um "save" no cliente degradaria o
loop desenhar→ver o dado e duplicaria no front a lógica de família/escopo/ids.
O autosave permanece; o que se quer é **nomear pontos no tempo** sobre o log
que já existe.

Duas perguntas de semântica acompanham a decisão:

- o que significa "voltar a um checkpoint" quando revisões posteriores
  conflitam (alguém editou um gate depois do ponto);
- o que acontece com gates recriados por revert de delete (o log guarda
  snapshot, mas o gate original foi removido fisicamente).

## Decisão

**Checkpoint é uma entidade de primeira classe** (`AnalysisCheckpoint`):
experimento, a revisão que marca o ponto, mensagem opcional, autor e
timestamp. Não é um flag na revisão porque o checkpoint é candidato a virar
molde no futuro (ponte com a visão de workspaces/templates do BE-19).

**Autosave não muda.** Cada operação continua gravando sua revisão
imediatamente; o checkpoint apenas aponta para a última revisão do momento.
Não existe estado "não salvo" — fechar a página nunca perde trabalho, e o
prompt de saída deixa de ser necessário.

**Dois tipos de ponto de retorno, um só mecanismo.** O alvo de qualquer
restore é sempre uma *revisão* ("desfazer tudo depois dela"); sobre isso
existem duas granularidades:

- **Auto-checkpoint (temporal, derivado):** a timeline agrupa revisões em
  sessões por janelas de atividade — bursts separados por inatividade acima
  de um limiar viram grupos ("hoje 14:20–14:45, 12 alterações"). É uma visão
  computada na leitura sobre o log: nada é escrito, não precisa de
  scheduler, e o limiar pode ser ajustado sem migração. Qualquer borda de
  sessão é um ponto restaurável.
- **Checkpoint fixado (entidade):** "pin" de uma borda automática ou "salvar
  ponto" com mensagem — ambos criam um `AnalysisCheckpoint` apontando para
  a revisão que fecha o ponto. Persiste nomeado e é o candidato a molde
  (BE-19).

Ou seja: toda revisão já é implicitamente um ponto de retorno; a sessão dá
navegação temporal de graça; o checkpoint dá identidade permanente ao ponto
que importa.

**Restaurar um checkpoint = reverter em cadeia**, na ordem inversa, todas as
revisões posteriores à revisão marcada:

- `dry_run` calcula o plano composto (reuso de `plan_revert` por revisão) e
  devolve `would_change` + `conflicts` agregados;
- o restore real é **atômico**: qualquer conflito bloqueia com `409` —
  "tudo ou nada" é o comportamento padrão;
- quem tem permissão de edição pode passar `force=true` ("Sobrescrever
  alterações" na UI) para restaurar o estado do checkpoint **mesmo sobre
  conflitos** — escape explícito, registrado no log como o resto;
- a restauração inteira grava **uma** revisão nova (`action="restore"`,
  `reverts` → a revisão marcada, `payload_before` com o antes de cada
  operação revertida) — o log continua append-only e legível ("restaurou o
  checkpoint X"), em vez de N revisões de revert.

**Gates recriados voltam com ids novos e recálculo automático.** O delete de
gate é físico (CASCADE); o restore recria a subárvore a partir do snapshot —
não é undelete, é re-criação. `parent`/`copied_from` dentro da subárvore são
remapeados (`id_map`); referências `copied_from` externas apontando para
dentro do que foi deletado já foram consumidas por `SET_NULL` e não voltam.
Após recriar, o restore dispara `recalculate_gate_analysis` para os gates
novos — incluindo a marcação de não-avaliável do ADR-0016 quando o canal não
existir na amostra. O estado restaurado é equivalente, não idêntico: o log é
a testemunha do que realmente aconteceu.

**Permissões:** quem enxerga o experimento lê o histórico e os checkpoints
(transparência do log para todo o laboratório); criar checkpoint e restaurar
exigem `can_edit_experiment` (editor, dono, admin).

## Alternativas consideradas

### A) Draft no cliente + save atômico com mensagem (modelo Grafana)

Descartada: stats e density são computados no servidor, então edições locais
não teriam dado até o save; o front teria que reimplementar família de
cópias, escopo de propagação e ids provisórios. Custo desproporcional para
um ganho que o log append-only já cobre.

### B) Checkpoint como flag/campo na `AnalysisRevision`

Descartada pela ponte com BE-19: um ponto nomeado que no futuro pode ser
promovido a template de análise precisa de identidade própria. Como flag,
viraria reuso torto de um registro de evento.

### B2) Auto-checkpoints materializados no banco

Descartada: gravar uma linha de checkpoint a cada burst exigiria gatilho de
escrita (regra de inatividade embutida na gravação da revisão ou um job —
e não temos worker), política de expurgo para o ruído, e a regra ficaria
congelada no dado escrito. O agrupamento temporal derivado na leitura
entrega o mesmo resultado com custo zero de escrita — e "pin" materializa
só o ponto que o usuário considerou importante.

### C) Restore parcial (reverte o que dá, reporta o resto)

Descartada como padrão: meio-restore deixa a árvore num estado que não é nem
o atual nem o do checkpoint — pior que não voltar. O `force` cobre o caso
"sei o que estou fazendo" sem intermediário perigoso.

### D) Recriar gates preservando o id original

Descartada: exigiria `INSERT` com pk forçado e referências fantasmas enquanto
o alvo não existe; e esconderia a verdade — o gate foi deletado. Ids novos
mantêm o log honesto e o `id_map` já resolve a subárvore.

## Consequências

- O histórico ganha uma segunda granularidade: evento (revisão) e marco
  (checkpoint). A UI precisa mostrar os dois sem confundir — ver FE-25.
- `restore` em cadeia herda os limites do revert unitário: operações sem
  payload suficiente para inverter (se aparecerem) serão conflito permanente;
  instrumentação nova precisa continuar gravando `payload_before` completo.
- `force=true` sobrescreve trabalho posterior ao checkpoint — o log registra,
  mas a UI deve exigir confirmação explícita e nomear o que será perdido.
- Restore recria gates e recalcula na request: experimentos grandes podem
  tornar a operação lenta. Se doer, a conversa é processamento assíncrono —
  ADR novo, não workaround.
- Checkpoint ainda não é template: não há compartilhamento entre experimentos
  nem instanciação — a ponte com BE-19 é apenas a forma do modelo, não
  funcionalidade prometida.
