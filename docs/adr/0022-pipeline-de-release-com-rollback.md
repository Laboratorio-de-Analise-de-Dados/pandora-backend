# ADR-0022 — Pipeline de release por tag com rollback automático de código

- **Status:** Aceito
- **Data:** 2026-09-16
- **Contexto do código:** `.github/workflows/`, `docker-compose.prod.yml`,
  `pandora/urls.py` (endpoint `/health/`)

## Contexto

O pipeline anterior disparava build+deploy a partir de qualquer push na
`main`, com três fragilidades concretas:

1. `main` era ao mesmo tempo linha de integração e candidata a produção —
   não havia noção de "versão liberada".
2. `migrate` rodava **dentro** do container na subida: migration quebrada
   derrubava o serviço no boot, e o job de deploy reportava sucesso mesmo
   com o gunicorn morto.
3. Não existia health check nem registro da versão anterior — rollback era
   manual, por memória ("qual era a tag boa?"), e o `latest` era
   sobrescrito a cada deploy independente de sucesso.

O projeto amadureceu para um fluxo de release versionado com política de
rollback em caso de falha de deploy.

## Decisão

**Release por tag, migrate fora do boot, rollback automático de código
com health check.** Concretamente:

- **CI (`ci.yml`)** roda `manage.py test` + `check` em todo push/PR para
  `main` — a suíte deixa de ser responsabilidade só local.
- **Release (`release.yml`)** dispara em `release: published` — ou seja,
  publicar uma **GitHub Release** na UI (Releases → Draft new release →
  "Create new tag `v*`" → "Generate release notes" → Publish). A tag é
  criada no publish; tag avulsa via `git tag` **não** deploya. As notas
  são geradas das PRs mergeadas no range, categorizadas por
  `.github/release.yml`. O workflow builda a imagem com a tag da versão
  (sem `latest` no push), roda as migrations num container **one-off**
  (`docker compose run --rm`) **antes** de trocar o tráfego — migration
  quebrada aborta o deploy com a versão anterior ainda no ar. Depois sobe
  a nova versão, espera `GET /health/` responder 200 e, se falhar,
  redeploya automaticamente a tag anterior (gravada em
  `.deployed_version` no servidor). `latest` só é publicado no Docker Hub
  **após** o deploy saudável.
- **Rollback manual (`rollback.yml`)**: `workflow_dispatch` recebendo uma
  tag → redeploya essa versão. Cobre o caso "deploy passou mas a feature
  veio errada".
- **Banco não tem rollback automático.** Política: migrations novas devem
  ser retrocompatíveis com a versão anterior do código (expand-contract:
  nunca dropar coluna/tabela na mesma release que para de usá-la). Backup
  do Postgres é rotina de infraestrutura (dump agendado no servidor), não
  step do pipeline.
- O portão manual do environment `production` é **mantido** — release
  aprovada ainda espera "Approve" para subir.

## Alternativas consideradas

### A) `pg_dump` + restore automático no rollback de deploy

Cobre migração destrutiva, mas restaurar dump em produção durante um
incidente é operação pesada e arriscada (perda de dados escritos após o
deploy, tempo de restore). Descartado como automação; a disciplina de
migração retrocompatível + backup agendado cobre o risco com menos
superfície de falha.

### B) Deploy a cada merge na main (sem tags)

Mantém o acoplamento integração↔produção que motivou a mudança.
Descartado.

### C) Blue-green / duas instâncias do app

O servidor atual roda um container só; blue-green dobra memória e exige
proxy na frente. Complexidade desproporcional ao estágio do projeto.

## Consequências

- Release é um ato explícito (publicar Release na UI, que cria a tag) —
  `main` pode integrar trabalho em progresso sem publicar.
- Falha de migration não derruba produção; falha de boot reverte sozinha.
- **Dívida aceita:** uma migration que quebra compatibilidade com a versão
  anterior pode deixar o rollback automático ineficaz (código velho contra
  schema novo). Mitigação é disciplina de expand-contract, a ser cobrada
  em review — vale reforçar no AGENTS.md.
- Rollback manual depende das tags de imagem continuarem publicadas no
  Docker Hub — `docker image prune` no servidor só afeta cache local.
- O front (`pandora-front`) segue o mesmo modelo sem o passo de migrate.
