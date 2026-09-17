# BE-08 — Histórico da análise: quem mudou, o quê, quando, e reverter

**Repo:** pandora-backend (+ front) · **Item do doc:** — (pedido em 12/09, ampliado em 13/09) · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/analysis-history`
**Status:** backend implementado em `fix/gate-density-missing-channel`; painel de histórico do front é MR à parte.
**ADR:** [0008](../adr/0008-historico-append-only-de-analise.md) (Proposto)

## Problema

A estratégia de análise só existe no estado atual. Quando várias pessoas do
laboratório trabalham no mesmo experimento, ninguém sabe quem alterou o quê, e
nenhuma alteração pode ser desfeita. As decisões que já tomamos agravam isso:
sobrescrever ao aplicar sobre nome existente, desanexar a cópia no reshape e
propagar no escopo do experimento são todas destrutivas e silenciosas.

A referência pedida é a de um documento colaborativo ("tipo Google Docs"): ver
quem editou cada parte da análise e quando, revisar as edições e decidir que uma
delas precisa voltar.

## Escopo

### 1. Registro (append-only)

`AnalysisRevision`:

| campo | conteúdo |
|---|---|
| `experiment` | FK (índice; toda leitura é por experimento) |
| `target_type` / `target_id` | `gate` \| `subsample` \| `file` \| `experiment` |
| `action` | `create`, `update_geometry`, `rename`, `recolor`, `apply`, `delete`, `disable`, `enable`, `move_subsample`, `revert` |
| `scope` | `file` \| `subsample` \| `experiment` (o escopo pedido na operação) |
| `payload_before` / `payload_after` | JSON só dos campos tocados |
| `affected_ids` | ids afetados quando a operação foi em lote |
| `user` / `created_at` | autor e momento |
| `reverts` | FK nullable para a revisão que esta desfaz |

Uma operação do usuário = **uma** revisão, mesmo quando toca N gates
(`affected_ids`). Pontos de escrita: criação/edição/exclusão de gate, apply,
disable/enable de amostra, criação/rename/inativação de subsample, mover
amostra entre subsamples.

### 2. Leitura (auditoria e revisão)

```
GET /analytics/experiment/<id>/history/?target=gate:51&user=<id>&cursor=...
→ [{ id, action, scope, target, summary, author, created_at, revertible }]

GET /analytics/history/<revision_id>/
→ revisão + diff legível (antes/depois por campo) + o que seria alterado ao reverter
```

- `summary` é frase pronta para a UI ("Paulo renomeou `P1` → `CD4+` em 5
  amostras"), montada no backend para não duplicar regra no front.
- Paginação por cursor; filtro por alvo, autor e intervalo de datas.
- Autorização: quem pode ler o experimento lê o histórico.

### 3. Reversão

```
POST /analytics/history/<revision_id>/revert/   { "dry_run": true }
→ { "would_change": [...], "conflicts": [...] }
```

- Reverter **não reescreve** o log: aplica o inverso e grava uma revisão nova com
  `action="revert"` e `reverts=<id>`.
- `dry_run` primeiro, com a mesma confirmação do
  [ADR-0002](../adr/0002-escopo-explicito-de-propagacao.md).
- Conflito (o alvo mudou depois, foi excluído, ou o nome antigo já está em uso) →
  reversão bloqueada com o motivo, nunca aplicada parcialmente às cegas.
- Reverter uma operação em lote reverte a operação inteira; reverter só uma
  amostra é uma edição normal, não um revert.

### 4. Front (MR à parte)

- Painel "Histórico" no experimento, agrupado por dia, com autor, hora e o
  `summary`; filtro por amostra/gate e por pessoa.
- Autor e data no hover da árvore de gates já existem (`created_by`); o painel é
  a visão completa.
- Reverter abre o diff e a lista de impacto antes de confirmar.

## Regras

- O log nunca é editado nem apagado ([ADR-0005](../adr/0005-api-nunca-deleta.md)).
- Nenhuma feature pode depender do log para saber o estado atual — o estado
  continua nas tabelas de domínio.
- Toda mutação de análise passa a ter registro obrigatório; endpoint novo sem
  registro é buraco no histórico (revisar em code review).

## Critérios de aceite

- [ ] Criar, remodelar, renomear, aplicar e excluir gate geram uma revisão cada,
      com autor, timestamp e antes/depois.
- [ ] Operação em lote gera **uma** revisão com todos os `affected_ids`.
- [ ] Mover amostra de subsample e desabilitar amostra aparecem no histórico.
- [ ] `GET .../history/` devolve em ordem cronológica inversa, paginado e
      filtrável por alvo e autor.
- [ ] `revert` com `dry_run` lista o impacto; sem `dry_run` restaura o estado
      anterior e grava a revisão de reversão.
- [ ] Revert de alvo já excluído/alterado → erro explicando o conflito, sem
      alteração persistida.
- [ ] Histórico visível só para quem tem acesso ao experimento.

## Fora de escopo (nesta fase)

- **Snapshot/estratégia nomeada** da árvore (workspace do FlowJo): reaplicar uma
  estratégia inteira em outra amostra/experimento. Vem depois, em cima do log.
- Merge de edições concorrentes: continua "último a escrever ganha"; o log torna
  visível, não resolve.
- Comentários/aprovação por trecho da análise (o lado "review" do Google Docs).
- Expurgo/retenção do log.
