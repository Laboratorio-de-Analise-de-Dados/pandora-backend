# AGENTS.md — pandora-backend

Guia operacional para agentes de IA. Leia antes de codar.

## Comandos

```bash
# setup local (uma vez)
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# desenvolvimento (Docker recomendado — sobe API + Postgres + Redis + Celery)
docker network create pandora_net   # apenas na primeira vez
docker compose up --build           # API em http://localhost:8085 (WEB_PORT)

# local sem Docker (precisa de Postgres + Redis rodando)
python manage.py migrate
python manage.py runserver
celery -A citosharp worker -l info

# verificação — rodar antes de concluir qualquer tarefa
TEST=1 python manage.py test        # SQLite, sem depender de Postgres/Redis
black <arquivos tocados>            # formatter oficial
python manage.py check              # sanity check do Django
```

- **CI não roda testes** — o workflow (`.github/workflows/ci.yml`) só builda e
  publica a imagem Docker; deploy exige aprovação manual no environment
  `production`. Teste local é responsabilidade sua.
- `manage.py migrate`/`makemigrations` rodam automático no compose — mas
  migration nova de model **deve** ser gerada e commitada junto.

## Arquitetura (3 apps)

```
accounts/     Auth JWT, usuários, organizações, RBAC, convites
fcs_parser/   Upload chunked, parsing FCS, FileData, Parquet, subsamples
analytics/    Gates, estatísticas, density/heatmap, escopo de propagação
```

- Lógica pesada → tasks Celery. Lógica de negócio → `fcs_parser/services/`
  e `analytics/` (nunca na view).
- **Validação de payload em serializer**, não inline na view (ADR-0009).
- Permissão explícita: `IsAuthenticated` + escopo por organização/criador.
- Storage em camadas: **ZIP é a fonte da verdade** (L0); .fcs extraído é
  efêmero; Parquet é cache regenerável; Redis é cache quente (ADR-0004).

## Regras duras

- **A API nunca deleta** — soft delete (`active=false`) sempre (ADR-0001/0005).
- Nunca commitar `.env`, `uploads/`, `db.sqlite3` ou credenciais.
- `SECRET_KEY`, senhas de banco e SMTP vêm de env/secrets — nunca hardcoded.
- Commits no padrão `feat(escopo):` / `fix(escopo):` (conventional).
- Um MR por PRD; base `main`.

## Documentação

- `docs/prd/BE-XX-*.md` — o **que** a entrega faz (um PRD por MR).
- `docs/adr/XXXX-*.md` — o **por quê** das decisões. Antes de tocar em modelo
  de dados, escopo de propagação ou storage, leia os ADRs relevantes —
  especialmente 0004 (ZIP fonte de verdade), 0005 (nunca deleta), 0006
  (identidade amostra/subsample) e 0009 (validação em serializers).
- ADR aceito **nunca** é editado: decisão nova = ADR novo com
  `Substitui ADR-XXXX`.
- Endpoints novos: documentar com `@extend_schema` (drf-spectacular).

## Relação com o front

A UI vive em `pandora-front` (React + Vite). PRDs de front são `FE-XX` com
numeração própria; referencie ADRs cruzados por nome
(`pandora-backend/docs/adr/0006`), nunca duplique o texto.
