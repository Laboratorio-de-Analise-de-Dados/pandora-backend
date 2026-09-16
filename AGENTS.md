# AGENTS.md — pandora-backend

Guia operacional para agentes de IA. Leia antes de codar.

## Comandos

```bash
# setup local (uma vez)
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencher SECRET_KEY e DATABASE_*

# desenvolvimento (Docker recomendado — sobe API + Postgres)
docker network create pandora_net   # apenas na primeira vez
docker compose up --build           # API em http://localhost:8085 (WEB_PORT)

# local sem Docker (precisa só de Postgres rodando)
python manage.py migrate
python manage.py runserver

# verificação — rodar antes de concluir qualquer tarefa
python manage.py test               # precisa do Postgres (ArrayField);
                                    # com compose: DATABASE_HOST=localhost
black <arquivos tocados>            # formatter oficial
python manage.py check              # sanity check do Django
```

- **Sem Celery/Redis** — removidos. Processamento pesado é síncrono, na
  request (ex-tasks viraram funções em `*/tasks.py` e `management/commands/`).
  Não reintroduzir broker sem ADR novo.
- **CI não roda testes** — o workflow (`.github/workflows/ci.yml`) só builda e
  publica a imagem Docker; deploy exige aprovação manual no environment
  `production`. Teste local é responsabilidade sua.
- O compose aplica `migrate` na subida; `makemigrations` é manual:
  `docker compose exec web python manage.py makemigrations` (o dev não
  precisa de Django na máquina). O Dockerfile roda
  `makemigrations --check` — build falha se model mudou sem migration.

## Arquitetura (3 apps + utils)

```
accounts/     Auth JWT, usuários, organizações, RBAC (Membership/Role), convites
fcs_parser/   Upload chunked, parsing FCS, FileData, Parquet, subsamples
analytics/    Gates, estatísticas, density/heatmap, escopo de propagação
utils/        Pacote compartilhado na raiz (density, validators, mixins)
pandora/    Projeto Django (settings, urls raiz, wsgi/asgi)
```

- Lógica de negócio → `fcs_parser/services/` e `analytics/` (nunca na view).
- **Validação de payload em serializer**, não inline na view (ADR-0009).
  `inline_serializer` do drf-spectacular é só documentação — não valida.
- Permissão explícita: `IsAuthenticated` + escopo centralizado em
  `fcs_parser/permissions.py` — leitura via `experiments_visible_to(user)`,
  escrita via `can_edit_experiment` / `can_move_experiment`.
- Storage em camadas: **ZIP é a fonte da verdade** (L0); .fcs extraído é
  efêmero; Parquet é cache regenerável (L2); cache quente é `FileBasedCache`
  em disco, não Redis (ADR-0004).

## Convenções — respeitar o framework

- View magra: `permission_classes` + `get_queryset` **escopado** + serializer.
  Buscar por id sem escopo vira IDOR — use
  `get_object_or_404(experiments_visible_to(self.request.user), id=...)`,
  nunca `Model.objects.get(id=...)` cru (vaza objeto alheio e devolve 500
  em vez de 404).
- Invariantes moram no banco (`UniqueConstraint` condicional em
  `Meta.constraints`), não só em código de view; `IntegrityError` na borda
  vira 400.
- Erros de domínio → `serializers.ValidationError` ou `Response` 4xx com
  `detail`; exceção não tratada nunca é resposta de API.
- Código usado por 2+ apps → `utils/` da raiz; código de um app → dentro
  dele (`services/`, `permissions.py`, `tasks.py`).
- `path()` de id usa `<int:...>` e `name=` quando a rota é referenciada.
- **Timestamps sempre UTC**: `USE_TZ=True` + `TIME_ZONE="UTC"` são fixos —
  banco grava UTC e a API emite ISO-8601 `Z`; a conversão para o fuso do
  usuário é responsabilidade do front. Não mude `TIME_ZONE` nem emita
  datetime naive/formatado em resposta.

## Regras duras

- **A API nunca deleta** — soft delete (`active=false`) sempre (ADR-0001/0005).
- Nunca commitar `.env`, `uploads/`, `staticfiles/`, `db.sqlite3` ou
  credenciais (o `.gitignore` já cobre).
- `SECRET_KEY`, senhas de banco e SMTP vêm de env/secrets — nunca hardcoded.
- Commits no padrão `feat(escopo):` / `fix(escopo):` (conventional).
- Um MR por PRD; base `main`.
- **Nunca trabalhe direto na `main`** — ao receber um pedido de mudança,
  primeiro `git checkout main && git pull` e crie uma branch
  (`feat/*`, `fix/*`, `refactor/*`, `docs/*`). Commit só na branch.

## Sessões paralelas

Vários agentes podem trabalhar neste repo ao mesmo tempo. **Antes de
codar, leia `docs/TRACKER.md`** — ele lista o que está em andamento, quem
é o responsável e qual área de arquivos cada frente toca. Se a sua
feature conflita com uma linha ativa, pegue outra ou alinhe antes. Ao
assumir trabalho novo, registre sua linha no tracker no mesmo commit;
ao concluir, atualize.

## Documentação

- `docs/prd/BE-XX-*.md` — o **que** a entrega faz (um PRD por MR).
- `docs/adr/XXXX-*.md` — o **por quê** das decisões. Antes de tocar em modelo
  de dados, escopo de propagação ou storage, leia os ADRs relevantes —
  especialmente 0004 (ZIP fonte de verdade), 0005 (nunca deleta), 0006
  (identidade amostra/subsample) e 0009 (validação em serializers).
- ADR aceito **nunca** é editado: decisão nova = ADR novo com
  `Substitui ADR-XXXX`.
- Endpoints novos: documentar com `@extend_schema` (drf-spectacular).

## Dívidas conhecidas (não usar como precedente)

- Validação manual de campo restante em `FileSubsampleView` — migrar para
  serializer quando tocar (ADR-0009).
- Processamento pesado roda na request; se passar a doer (timeout de
  upload/parse grande), a conversa é ADR novo, não workaround em view.
- A lista completa vive em `docs/prd/README.md` → "Dívidas registradas".

## Relação com o front

A UI vive em `pandora-front` (React + Vite). PRDs de front são `FE-XX` com
numeração própria; referencie ADRs cruzados por nome
(`pandora-backend/docs/adr/0006`), nunca duplique o texto.
