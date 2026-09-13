# BE-15 — Renomear pacote do projeto `citosharp` → `pandora`

**Repo:** pandora-backend · **Tipo:** refactor · **Base:** `main`
**Status:** implementado em `feat/scoped-lookups`.

## Problema

O pacote de configuração do Django chamava-se `citosharp/` — nome morto de
uma fase anterior do produto. O repo, o produto e os PRDs já dizem
"pandora"; o nome divergente confunde quem chega (`manage.py` apontando
para `citosharp.settings`, gunicorn servindo `citosharp.wsgi`).

## Escopo

Rename mecânico do pacote e de todas as referências — nenhuma mudança de
comportamento, modelo ou endpoint:

- `citosharp/` → `pandora/` (`settings.py`, `urls.py`, `wsgi.py`,
  `asgi.py`, `__init__.py`)
- `manage.py` — `DJANGO_SETTINGS_MODULE=pandora.settings`
- `pandora/settings.py` — `ROOT_URLCONF`, `WSGI_APPLICATION`
- `Dockerfile` — `DJANGO_SETTINGS_MODULE` e `CMD gunicorn pandora.wsgi:application`
- `docker-compose.prod.yml` — comando do gunicorn
- `AGENTS.md` e `README.md` — estrutura de diretórios

## Fora de escopo

- Nome do container/imagem Docker (`pandora_web`, `pandora_db` já dizem
  pandora — sem mudança necessária).
- `settings.py` por dentro (módulos `CITOSHARP_*` não existem; variáveis
  de ambiente não mudam).

## Risco

Deploy precisa subir com a imagem nova (o `gunicorn pandora.wsgi` não
existe na imagem antiga). Rollback = imagem anterior, sem migração de
dados envolvida.
