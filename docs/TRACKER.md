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
| BE-19 (derivação) | `POST /experiment/<id>/derive-analysis/` (ADR-0021): match por `content_guid`/`file_name`, cópia de árvore + subsamples homônimos + compensação aplicada; revisões `derive`/`create` revertíveis |

## Livres para pegar

| Feature | Observação |
|---|---|
| FE: UI de "derivar análise" (BE-19) | Backend pronto — consumir `POST /experiment/<id>/derive-analysis/` no front |
| BE-19 workspaces/templates (objeto persistido) | Visão no PRD — ADR-0021 escolheu snapshot sem template; reabrir só se o caso de uso pedir catálogo |
| BE-23 análise colaborativa (branch/merge) | PRD de visão pronto — precisa de ADR (estado visível de branch, merge de árvores) |
