---
name: release
description: Criar uma GitHub Release (tag + notas) e acompanhar o pipeline de deploy — usar quando o usuário pedir "nova versão", "criar release" ou "fazer deploy"
argument-hint: "[patch|minor|major ou versão exata, ex.: v1.2.0]"
allowed-tools:
  - exec
  - read
  - grep
  - glob
---

Publicar uma release deste repo (backend) e conduzir o deploy.

Modelo (ADR-0022): publicar uma **GitHub Release** cria a tag `vX.Y.Z` e
dispara `.github/workflows/release.yml` — build da imagem → migrate
one-off → health check → rollback automático → `latest` no Hub. Tag
avulsa via `git tag` **não** deploya. Deploy passa pelo portão do
environment `production` (aprovável via API, ver passo 5).

## Passo 0 — produção não é ambiente de teste

Antes de publicar, tudo que entra na release já tem que estar
**verificado**: CI verde na `main`, testes/verificação local do que
mudou, QA e review fechados. Depois do deploy só se confere o *deploy*
— container subiu, health check, versão certa no ar — nunca a feature.

E pipeline novo também é código não testado: se `release.yml` ou
`rollback.yml` mudaram desde o último deploy bem-sucedido, exercite o
caminho antes de confiar nele — `workflow_dispatch` numa tag já
publicada é idempotente e serve como ensaio do deploy.

## Passo 1 — descobrir a última versão e o que entra

```bash
gh release list -L 5
git fetch origin --tags
git log --oneline $(git describe --tags --abbrev=0 2>/dev/null || echo HEAD~50)..origin/main
gh pr list --state merged --limit 20 --json number,title,mergedAt,labels
```

Montar a lista das PRs/commits que entraram desde a última tag.

## Passo 2 — propor a versão (semver)

| O que entrou | Bump |
|---|---|
| Só fixes/docs/ci | patch (`v1.0.0` → `v1.0.1`) |
| Features novas | minor (`v1.0.0` → `v1.1.0`) |
| Quebra de contrato/compatibilidade | major (`v1.x` → `v2.0.0`) — confirmar com o usuário |

Se o usuário passou a versão/escopo explicitamente, usar o que ele pediu.
**Sempre mostrar a lista do que entra + a versão proposta e confirmar
antes de publicar** — release dispara deploy real.

## Passo 3 — publicar a release

```bash
gh release create vX.Y.Z --target main --title "vX.Y.Z" --generate-notes
```

`--generate-notes` usa o `.github/release.yml` (categorias por label).
Se as notas saírem ruins (PRs sem label), edite:
`gh release edit vX.Y.Z --notes-file notas.md`.

## Passo 4 — acompanhar o pipeline

```bash
gh run list --workflow release.yml --limit 3
gh run watch <run-id>
```

O job `deploy` pausa no portão `production` — o run fica `waiting`.

## Passo 5 — aprovar o deploy (se o usuário pedir)

```bash
# achar o deployment pendente do run
gh api repos/{owner}/{repo}/actions/runs/<run-id>/pending_deployments

# aprovar (environment_ids vem do GET acima)
gh api repos/{owner}/{repo}/actions/runs/<run-id>/pending_deployments \
  -X POST -f state=approved -f comment="aprovado via cli" \
  -F "environment_ids[]=<id>"
```

Alternativa: o usuário aprova na UI (Actions → run → Review deployments).

## Passo 6 — verificar

- `gh run watch` até o fim: migrate → up → health check → `latest`.
- Se falhar no health check, o workflow já reverte sozinho — reportar o
  log ao usuário e conferir `.deployed_version` no servidor se necessário.
- Rollback manual para uma tag antiga:
  `gh workflow run rollback.yml -f tag=vX.Y.Z` (também passa pelo portão).

## Coordenação front/back

- Versões são **independentes** (`v*` em cada repo).
- Se a release muda contrato de API (endpoint novo/removido), a release do
  **backend vai antes**; avisar o usuário para rodar a release do front
  depois (a skill equivalente existe em `pandora-front/.devin/skills/`).

## Passo 7 — atualizar o board

Deploy verificado = os cards das features que entraram na release vão
para 🚀 Em produção, com comentário citando a tag. O que está mergeado
mas ainda não saiu em release permanece em 📦 Entregue. Convenções do
board na skill `/trello-board`.
