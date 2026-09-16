# Tracker de trabalho em andamento (multi-sessão)

Vários agentes/sessões podem trabalhar neste repo em paralelo. **Antes de
começar uma feature, leia este arquivo**: se a área já tem dono, escolha
outra que não conflite (conflito = mesmos arquivos, modelos ou endpoints)
ou alinhe com o responsável.

- Ao **assumir** uma feature: adicione/atualize sua linha e commite junto.
- Ao **terminar**: marque ✅ (ou remova a linha).
- Uma linha por frente de trabalho; "área" = arquivos/contrato afetado.

## Em andamento

| Feature | Área afetada | Branch | Sessão | Desde |
|---|---|---|---|---|
| — | — | — | — | — |

## Concluído nesta branch

| Feature | Observação |
|---|---|
| BE-18/17/08/20/21 | Histórico, checkpoints, metadados da listagem — implementados |
| BE-22 | Compensação completa: `compensated` + `embedded` + `CompensationMatrix` + controles via subsample + `compute`/`from-header`/`apply`/`remove` + leitura compensada em density/stats/preview/list/gates (cache key com id da matriz) |

## Livres para pegar

| Feature | Observação |
|---|---|
| BE-19 workspaces/templates | Precisa de ADR de domínio antes (visão no PRD) |
| BE-23 análise colaborativa (branch/merge) | PRD de visão pronto — precisa de ADR (estado visível de branch, merge de árvores) |
