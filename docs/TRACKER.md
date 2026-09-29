# Tracker de trabalho em andamento (multi-sessão)

> **Status de features NÃO vive aqui** — pipeline (backlog/fazendo/
> entregue/produção), prioridade e checklists de progresso estão no
> Trello, board **"Pandora — Implementações"**
> (https://trello.com/b/dXb21KpL). Cada card linka seu PRD/ADR na
> descrição. Este arquivo é só **coordenação entre sessões paralelas**
> (quem está tocando qual área de arquivos agora).

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
| BE-32/ADR-0028 dedup de storage (docs) | `docs/prd`, `docs/adr` | `docs/be-32-blob-dedup` | devin | 2026-09 |
| BE-36 preview de compensação ad-hoc (card #82) | ✅ concluído — mergeado (PRs #119/#121), deploy v0.5.0 | — | — | — |
| Dívida: validação inline → serializers (ADR-0009) + auditoria de stats (eixos de gate_coordinates com precedência, `n`/`rcv` por canal, `FileStatsView` reusando `calculate_cytometry_metrics`, `std_dev` sanitizado p/ n=1) + domínio de gate em `analytics/gate_filter.py` + remoção de código morto | `*/serializers.py`, `*/views.py`, `*/tests.py`, `analytics/gate_filter.py`, `utils/density.py`, `analytics/tasks.py`, `fcs_parser/services/*` | `refactor/inline-validation-serializers` | devin | 2026-09 |
| BE-34 tags de amostra (+ subsample/herança/setup) | ✅ concluído — mergeado (PRs #113/#114), deploy v0.4.0 | — | — | — |
| Fix e-mail de convite (FE-10 passo 1) | `accounts/services/send_mail.py`, `accounts/tests.py` | `fix/invite-email-url` | devin | 2026-09 |
| Docs: revisão do PRD BE-26 (marcação por arquivo + placa) | `pandora-docs/prd/BE-26*` | `docs/be-26-controles-arquivo` | devin | 2026-09 |
| plot_config da amostra raiz (card #71) | ✅ concluído — mergeado (PR #118) | — | — | — |
| BE-35 compensação manual (card #73) | ✅ concluído — mergeado (PR #119) | — | — | — |

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
| BE-33 (PRD) | Figuras de análise persistidas (spec + `result_cache` + `result_revision` + `is_stale` + `recompute/`) — documentado em `docs/be-33-figures`; implementação livre para pegar (consumida pelo FE-36 do front) |
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
| ~~Environment `production`~~ ✅ | Resolvido 2026-09: Required reviewers configurado nos dois repos (`paulo-moro` como reviewer). Deploy agora exige Approve no gate do environment |
| BE-26 identificar controles | Não iniciado — PRD revisado 2026-09: marcação por **arquivo** (`FileDataModel`), PATCH em lote, `well_id`/`suggested_control` na listagem; estende ADR-0019, consome `SampleTag`/`FileTag` do BE-34. Frente à parte junto do Juvia |
| ~~FE: UI de "derivar análise"~~ ✅ | FE-28 mergeado (front PR #86) — diálogo no menu do card + relatório pós-derivação |
| ~~FE: UI de branches~~ ✅ | FE-29 mergeado (front PR #88) — seletor `?branch=` no workspace, fork/renomear/arquivar, diff + merge com resolução de conflitos |
| BE-19 workspaces/templates (objeto persistido) | Visão no PRD — ADR-0021 escolheu snapshot sem template; reabrir só se o caso de uso pedir catálogo |
| BE-23: compensação por branch | v1 manteve matriz experiment-wide; `CompensationMatrix.branch` é o refinamento quando a UI pedir |
| BE-19 derivação: refinamentos | `dry_run`/preview + `file_mapping` explícito, `non_evaluable_gates` no report, `mode=replace`, `as_branch` — casos registrados na conversa do ADR-0021 |
