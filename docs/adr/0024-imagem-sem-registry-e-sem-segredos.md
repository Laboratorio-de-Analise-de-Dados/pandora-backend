# ADR-0024 — Pipeline de imagem sem registry e sem segredos

- **Status:** Proposto
- **Data:** 2026-09-17
- **Contexto do código:** `Dockerfile`, `.dockerignore`,
  `docker-compose.prod.yml`, `.github/workflows/release.yml`,
  `.github/workflows/rollback.yml`, `pandora/settings.py` (SIMPLE_JWT)

## Contexto

O pipeline do ADR-0022 publicava a imagem no Docker Hub com a
`SECRET_KEY` assada numa layer (`ARG` + `ENV` no Dockerfile, alimentada
por `--build-arg` no CI). Com o repositório `paulomoro/pandora-backend`
público no Hub, a chave que assina os JWTs (HS256 usa `SECRET_KEY` como
`SIGNING_KEY` por padrão) ficou acessível a qualquer pessoa via
`docker pull` — 38 tags públicas, cada uma uma cópia do segredo.

Complementares: o compose de produção publicava `5432` no host e o
`.dockerignore` não excluía `.env`, então um build local podia embutir
credenciais na imagem.

## Decisão

1. **Nenhum segredo entra na imagem.** O Dockerfile usa
   `SECRET_KEY=build-dummy` inline nos `RUN` que precisam dela
   (`makemigrations --check`, `collectstatic`); a real só chega em
   runtime via env do compose. `.dockerignore` passa a excluir `.env`,
   chaves, dados e docs internos.
2. **Nenhum registry.** A imagem viaja `docker save | gzip` →
   artifact do Actions → `scp` → `docker load` no servidor. O Hub sai do
   caminho do deploy; `latest` passa a ser uma tag **local** no host,
   marcada só após deploy saudável.
3. **JWT ganha `SIGNING_KEY` própria** (`JWT_SIGNING_KEY`, opcional):
   desacopla a assinatura de tokens da `SECRET_KEY` do Django — blast
   radius menor e rotação independente. Sem a env, cai na `SECRET_KEY`
   (compatível com o comportamento anterior).
4. **Postgres sai do host**: `ports:` → `expose:` — o banco só existe
   dentro da `pandora_net`.
5. **Rollback sem pull**: `rollback.yml` valida que a tag existe no
   servidor (`docker image inspect`) e só troca `IMAGE_TAG`; o deploy
   mantém as últimas 5 tags `v*` no host.

Complementa o ADR-0022: gatilho (Release publicada), portão do
environment `production`, migrate one-off, health check e rollback
automático continuam iguais — muda o transporte e o conteúdo da imagem.

## Alternativas consideradas

### A) Repo privado no Docker Hub

O free tier permite 1 repo privado e ele já estava ocupado pelo front.
Não escala pro juvia (que vai deployar pelo mesmo padrão) e não resolve
o problema de fundo: segredo não deveria estar na imagem, pública ou
privada. Mantida como mitigação transitória até esta ADR deployar.

### B) Registry próprio no servidor (`registry:2` atrás do nginx)

É o "hub interno" correto para multi-servidor ou deploys frequentes,
mas adiciona um serviço com auth/TLS/backup pra manter. Com 1 host e
releases esporádicas, o tarball via SSH (já usado pelo deploy) cobre o
caso com zero infra nova. Fica como plano B documentado.

### C) GHCR privado

A quota de org free (storage/egress) estoura rápido com imagens de
~220MB por release. Descartada pelo custo/limites.

## Consequências

- A imagem deixa de ser artefato público: contém só código, e mesmo
  isso para de sair do pipeline.
- `DOCKER_USER`/`DOCKER_TOKEN` deixam de ser necessários no deploy —
  podem ser revogados quando front/juvia migrarem ao mesmo padrão.
- Rollback depende das imagens estarem no host (janela = últimas 5
  tags `v*`). Rollback além da janela = `release.yml` workflow_dispatch
  rebuildando a tag — mais lento, mas existe.
- Deploy passa a transferir ~220MB via SSH por release — irrelevante
  na cadência atual; se ficar diário, reavaliar a alternativa B.
- Dívida assumida: se um dia o repo ficar privado e quisermos imagens
  versionadas fora do host, volta a conversa de registry interno —
  este ADR é o ponto de partida.
