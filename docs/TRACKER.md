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
| BE-31 resiliência de processamento | `docs/prd/BE-31*`, `fcs_parser/services/*` | `docs/be-31-processing-resilience` | outra sessão | 2026-09 |

## Concluído nesta branch

| Feature | Observação |
|---|---|
| BE-18/17/08/20/21 | Histórico, checkpoints, metadados da listagem — implementados |
| BE-22 | Compensação completa: `compensated` + `embedded` + `CompensationMatrix` + controles via subsample + `compute`/`from-header`/`apply`/`remove` + leitura compensada em density/stats/preview/list/gates (cache key com id da matriz) |
| BE-19 (derivação) | `POST /experiment/<id>/derive-analysis/` (ADR-0021): match por `content_guid`/`file_name`, cópia de árvore + subsamples homônimos + compensação aplicada; revisões `derive`/`create` revertíveis |
| BE-23 (branches) | ADR-0020 implementado: `AnalysisBranch` + `branch`/`forked_from` em gates/revisões, fork materializado, `GET/POST .../branches/`, `PATCH/DELETE /analytics/branches/<id>/`, `GET .../diff/`, `POST .../merge/` (mine/theirs/both, dry_run, revert em cadeia), `?branch=` na timeline e na árvore de `list/data` |
| BE-24 | `POST /experiment/` cria experimento sem arquivo, `description` livre, `values` read-only (derivado dos FCS) |
| BE-29 | SocialAccount + AuthEvent + link/unlink + aviso de vínculo (ADR-0025/0026) — mergeado no PR #95 |
| BE-30 | Merge de contas: `POST /accounts/merge/confirm/`, `merge_accounts()` migra memberships/FKs/SocialAccount, conta absorvida inativa com `merged_into` — mergeado no PR #97 |
| Limpeza de branches (2026-09-17) | `fix/security-secret-image` rebaseada + PR #94 mergeada (ADR-0024 — imagem sem registry/segredos); branches obsoletas removidas: `feat/email-smtp-config` (SMTP já estava na main — `EMAIL_*` em settings + compose prod), `chore/dev-compose-juvia` e `chore/remove-juvia-service` (main já sem serviço juvia), `docs/prd-be-21-*` e `docs/adr-0022-*` (conteúdo já na main / cherry-pickado) |
| Sync de status de PRDs | README de PRDs corrigido: BE-19 parcial (derivação ok, template é visão), BE-23 implementado (ADR-0020), BE-25 substituído por BE-28 |

## Fora da branch (em PR própria)

| Item | Observação |
|---|---|
| Bump de segurança de deps | PR #84 **mergeada**: Django 5.2.16, DRF 3.17.2, PyJWT 2.13.0 etc. — resolveu os 52 alertas do dependabot. `main` já mergeada nesta branch + `pathspec==1.1.1` (exigido pelo black 26.3.1); suíte completa revalidada — 202 testes OK. |
| Trunk-based | `develop` removida; default do repo é `main`. Fluxo: `feat/*`/`fix/*` → PR → `main` |

## Livres para pegar

| Feature | Observação |
|---|---|
| Google SSO — ativação (fase 2) | Código pronto (BE-29); falta só OAuth client no Google Cloud + envs `GOOGLE_*`. Decisão 2026-09: ativar antes da virada para v1 em prod — ver seção "Ativação do Google" no PRD BE-29 |
| **Environment `production` sem required reviewers** | Descoberto na validação do release (2026-09): o gate `environment: production` existe no YAML mas o environment no GitHub só tem `branch_policy` — `workflow_dispatch` **deploya imediatamente, sem aprovação**. Configurar Required reviewers em Settings → Environments → production, ou tratar todo dispatch como deploy real |
| BE-26 identificar controles | Não iniciado — estende ADR-0019; fecha o ciclo da compensação (BE-22) |
| FE: UI de "derivar análise" (BE-19) | Backend pronto — consumir `POST /experiment/<id>/derive-analysis/` no front (PRD: `pandora-front/docs/prd/FE-28`) |
| FE: UI de branches (BE-23) | Backend pronto — seletor de branch (`?branch=` nas leituras), criar/renomear/arquivar, tela de diff + resolução de conflitos do merge (PRD: `pandora-front/docs/prd/FE-29`) |
| BE-19 workspaces/templates (objeto persistido) | Visão no PRD — ADR-0021 escolheu snapshot sem template; reabrir só se o caso de uso pedir catálogo |
| BE-23: compensação por branch | v1 manteve matriz experiment-wide; `CompensationMatrix.branch` é o refinamento quando a UI pedir |
| BE-19 derivação: refinamentos | `dry_run`/preview + `file_mapping` explícito, `non_evaluable_gates` no report, `mode=replace`, `as_branch` — casos registrados na conversa do ADR-0021 |
