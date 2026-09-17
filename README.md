# Pandora Backend

Backend da plataforma **Pandora** para análise de dados de **citometria de fluxo**.
Permite autenticação via JWT, gestão de organizações/usuários, upload e
processamento de arquivos **FCS** (em chunks) e criação/consulta de *gates* de
análise.

> Este repositório é a API consumida pelo
> [`pandora-front`](https://github.com/Laboratorio-de-Analise-de-Dados/pandora-front).

---

## Sumário

- [Stack](#stack)
- [Como rodar o projeto](#como-rodar-o-projeto)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Arquitetura](#arquitetura)
- [Guia de contribuição e decisões de código](#guia-de-contribuição-e-decisões-de-código)
- [Documentação interativa da API (Swagger / Redoc)](#documentação-interativa-da-api-swagger--redoc)
- [Visão geral das rotas](#visão-geral-das-rotas)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Deploy](#deploy)

---

## Stack

- **Python 3.13** / **Django 5.2** + **Django REST Framework**
- **JWT** (`djangorestframework-simplejwt`) para autenticação
- **PostgreSQL** como banco de dados
- **Pandas** + **PyArrow** para manipulação de dados e cache em Parquet
- **drf-spectacular** para geração automática da documentação OpenAPI
- **Docker** / **docker-compose** para ambiente local e produção
- **Black** para formatação de código

> Não há Celery/Redis: o processamento (parse FCS, recálculo de gates) é
> síncrono, na request. Reintroduzir fila exige ADR novo.

---

## Como rodar o projeto

### Com Docker (recomendado)

Sobe a API e o banco PostgreSQL de uma vez.

```bash
# 1. crie a rede externa usada pelo compose (apenas na primeira vez)
docker network create pandora_net

# 2. suba os serviços (já roda `migrate` automaticamente)
docker compose up --build
```

A API ficará disponível em `http://localhost:8085` (mapeada para a porta 8000 do
container). A porta pode ser alterada com a variável `WEB_PORT`.

> O compose roda só `migrate` na subida. Mudou um model? Gere a migration
> dentro do container (o volume `.:/app` escreve o arquivo de volta no host)
> e commite junto:
> ```bash
> docker compose exec web python manage.py makemigrations
> ```

### Localmente sem Docker

Pré-requisitos: Python 3.13 e uma instância de PostgreSQL.

```bash
# 1. ambiente virtual
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. dependências
pip install -r requirements.txt

# 3. configure o .env (veja a seção "Variáveis de ambiente")
cp .env.example .env

# 4. migrações e servidor
python manage.py migrate
python manage.py runserver
```

---

## Variáveis de ambiente

O projeto lê as variáveis de um arquivo `.env` na raiz (ou diretamente do
ambiente). As principais:

| Variável | Descrição |
| --- | --- |
| `SECRET_KEY` | Chave secreta do Django (obrigatória) |
| `DEBUG` | `True`/`False` (padrão `True`) |
| `ALLOWED_HOSTS` | Hosts permitidos, separados por vírgula (padrão `*`) |
| `DATABASE_NAME` | Nome do banco PostgreSQL |
| `DATABASE_USER` | Usuário do banco |
| `DATABASE_PASSWORD` | Senha do banco |
| `DATABASE_HOST` | Host do banco |
| `DATABASE_PORT` | Porta do banco |
| `MEDIA_ROOT` | Diretório onde os uploads são salvos |
| `PARQUET_MAX_IDLE_DAYS` | Dias sem acesso antes de limpar Parquet frio (padrão `7`) |
| `DENSITY_CACHE_TTL` | TTL (s) do cache de density em disco (padrão `3600`) |
| `CORS_ALLOWED_ORIGINS` | Origens permitidas, separadas por vírgula |
| `EMAIL_HOST_USER` | Usuário SMTP (envio de convites por e-mail) |
| `EMAIL_HOST_PASSWORD` | Senha SMTP |
| `TEST` | Se definida, usa SQLite em vez do PostgreSQL — **não roda a suíte de testes** (os models usam `ArrayField`, que é Postgres-only) |

---

## Arquitetura

### Visão geral

O backend é organizado em 3 Django apps com responsabilidades claras:

```
accounts/       Autenticação, usuários, organizações, RBAC
fcs_parser/     Upload, processamento e storage de dados FCS
analytics/      Gates, análises estatísticas, density/heatmaps
```

Lógica pesada (parsing de FCS, recálculo de gates) roda **sincronamente** na
request, em funções de `*/tasks.py` e `management/commands/` (sem Celery). O
cache de density/heatmap é `FileBasedCache` em disco (`MEDIA_ROOT/cache`).

### Modelo de dados principal

```
Organization (1) ──> (N) ExperimentModel
                              │
                              ├── zip_path (fonte da verdade)
                              ├── values (canais encontrados)
                              │
                              └── (1) FileModel (ZIP original)
                                        │
                                        └── (N) FileDataModel (um por .fcs)
                                                   │
                                                   ├── parquet_path (cache L2)
                                                   ├── headers (metadados FCS)
                                                   │
                                                   └── (N) GateModel
                                                              │
                                                              ├── gate_coordinates
                                                              ├── parent (hierarquia)
                                                              └── (1) AnalysisResult
```

### Storage: ZIP como fonte da verdade

O sistema usa uma arquitetura de cache em camadas onde o **ZIP original do
upload é a fonte da verdade imutável**:

```
L0  ZIP (MEDIA_ROOT/{id}.zip)     ← fonte da verdade, nunca deletado
L1  .fcs extraídos (efêmeros)     ← extraídos sob demanda, removidos após uso
L2  Parquet (MEDIA_ROOT/parquet/) ← cache morno, regenerável do ZIP
L3  FileBasedCache (MEDIA_ROOT/cache/) ← cache quente (density), TTL deslizante
```

**Fluxo de leitura** (`FileDataModel.get_dataframe()`):
1. Tenta Parquet no disco (L2)
2. Se expirou: extrai o `.fcs` específico do ZIP (L0), reparseia, regenera Parquet
3. Fallback legado: `fcs_path` direto (dados pré-migração)
4. Último recurso: `data_set` JSON no banco (dados muito antigos)

**Fluxo de upload** (chunked):
```
Frontend                         Backend
   │                                │
   ├── POST /experiment/init/  ──>  │  Cria ExperimentModel
   │                                │
   ├── POST /upload-chunk/ (N×) ──> │  Salva chunks em MEDIA_ROOT/chunks/
   │                                │
   └── POST /complete/  ────────>   │  assemble_chunks() → ZIP
                                    │  Cria FileModel
                                    │
                                    └── (síncrono) extract_metadata_from_zip()
                                         │  Extrai ZIP → /fcs_files/{id}/
                                         │  Para cada .fcs:
                                         │    parse → FCSResult
                                         │    cria FileDataModel + Parquet
                                         │  Salva zip_path no ExperimentModel
                                         └── Remove /fcs_files/{id}/ (efêmero)
```

### Camada de services

A lógica de negócio vive em `fcs_parser/services/`:

| Service | Responsabilidade |
| --- | --- |
| `process_fcs.py` | Parsing de um `.fcs` → `FCSResult` (dataclass tipada) |
| `process_experiment_file.py` | Pipeline completo: chunks → ZIP → parse → FileData + Parquet |
| `decompressor.py` | Extração de arquivos comprimidos |
| `header_parser.py` | Serialização de headers FCS |

**Regra**: views são finas (recebem request → delegam ao service → retornam
response). Toda lógica de negócio, I/O e manipulação de dados fica nos services.

### Gates e análise hierárquica

Gates seguem uma estrutura de árvore (pai → filhos). O cálculo de métricas
percorre toda a cadeia hierárquica do root até o gate alvo, aplicando filtros
sequencialmente — mesmo padrão do FlowJo/Cytobank:

```
Root Gate (file_data)
  └── Gate A (polígono)
        └── Gate B (retângulo)
              └── Gate C (polígono)
```

Para calcular métricas do Gate C: aplica filtro A → B → C sobre o dataset
original. Métricas calculadas: count, %Parent, %Total, MFI por canal, CV.

### Processamento pesado (síncrono)

| Função / comando | Gatilho | Descrição |
| --- | --- | --- |
| `process_experiment_zip` / `extract_metadata_from_zip` | `POST .../complete/` | Processa ZIP de upload → FileData + Parquet |
| `RecomputeFileDataView` | `POST /file/<id>/recompute` | Regenera Parquet de um FileData a partir do ZIP |
| `recalculate_gate_analysis` | `post_save` de GateModel | Recalcula métricas de um gate |
| `cleanup_cold_parquet` | `manage.py cleanup_parquet` (cron) | Remove Parquets órfãos e frios (>7d sem acesso) |

---

## Guia de contribuição e decisões de código

### Regras para abrir um PR

1. **Formate com Black** antes de commitar:
   ```bash
   black .
   ```

2. **Não quebre a API existente** — endpoints e formatos de resposta devem
   manter compatibilidade. Se precisar mudar, coordene com o front.

3. **Não altere URLs sem alinhamento** — as rotas atuais estão acopladas ao
   front. Mudanças de URL requerem PR coordenado.

4. **Inclua migrations** — se mexeu em models, gere a migration:
   ```bash
   python manage.py makemigrations
   ```
   Confira se a migration gerada faz sentido e comite junto.

5. **Não comite secrets** — nada de `.env`, tokens ou senhas no código.
   Use variáveis de ambiente.

6. **Teste a cascata de dados** — se mexeu no fluxo de
   processamento/storage, garanta que o pipeline upload → process → analyze
   funciona end-to-end.

### Onde colocar código novo

| Tipo de código | Onde vai |
| --- | --- |
| Lógica de negócio (parsing, processamento, cálculos) | `app/services/` |
| Receber request e retornar response | `app/views.py` |
| Definição de tabelas/campos | `app/models.py` |
| Validação de input da API | `app/serializers.py` |
| Escopo de acesso (quem vê/edita o quê) | `app/permissions.py` |
| Processamento pesado | `app/tasks.py` (função síncrona) ou `management/commands/` |
| Helpers reutilizáveis entre apps | `utils/` |

### Convenções de código

- **Views são finas**: recebem request, delegam a um service, retornam
  response. Se a view tem mais de ~15 linhas de lógica, extraia para um
  service.

- **Services retornam dataclasses tipadas**, nunca tuplas/listas genéricas
  ou mixed types (e.g. `list | str`). Erros são exceções, não valores de
  retorno.

- **Logging, não print**: use `logger = logging.getLogger(__name__)` em todo
  módulo. Nunca `print()` — logs estruturados permitem filtrar por nível e
  módulo.

- **`save(update_fields=[...])`**: sempre especifique os campos ao salvar
  parcialmente. Evita race conditions e deixa claro o que mudou.

- **Normalize colunas com o helper**: use `normalize_columns()` de
  `utils/density.py` em vez de repetir `.str.replace().str.lower()` manual.

- **Models devem ter `__str__`**: facilita debug no admin e nos logs.

### Decisões arquiteturais

Ao implementar algo novo, considere:

1. **ZIP é a fonte da verdade** — nunca delete o ZIP. Todo dado derivado
   (Parquet, .fcs extraído, JSON) é cache/efêmero e deve ser regenerável.

2. **Cache é descartável** — Parquet, FileBasedCache, .fcs extraído podem
   ser limpos sem perda. O código deve sempre ter um fallback para
   reconstruir a partir do ZIP.

3. **Processamento pesado hoje é síncrono** — mora em funções de
   `*/tasks.py`/`services/`, chamadas na request. Se uma operação nova
   passar de ~2s de forma recorrente, a conversa sobre fila é decisão de
   arquitetura (ADR novo), não implementação direta.

4. **Hierarquia de gates é uma árvore** — qualquer cálculo sobre um gate
   precisa percorrer toda a cadeia de pais. Nunca calcule métricas de um
   gate filho sem aplicar os filtros dos pais primeiro.

5. **Backward compatibility** — ao mudar modelos ou fluxos, mantenha
   fallback para dados antigos. Exemplo: `get_dataframe()` tem 4 níveis
   de fallback justamente pra não quebrar dados pré-migração.

6. **Uma única implementação por pipeline** — se a mesma lógica é usada
   em task + view + outro lugar, extraia pra um service. Nunca duplique
   pipelines de dados.

7. **Todo lookup é escopado** — buscar objeto por id passa por um queryset
   de `fcs_parser/permissions.py` (`experiments_visible_to`,
   `file_data_visible_to`, `uploads_visible_to`) ou
   `analytics/permissions.py` (`gates_visible_to`). Nunca
   `Model.objects.get(id=...)` cru numa view — vaza objeto alheio (IDOR)
   e devolve 500 em vez de 404. Ver ADR-0014.

### O que NÃO fazer

- Não crie endpoints REST fora do padrão sem motivo (evite `/list/data/`,
  prefira coleções aninhadas: `/experiments/{id}/files/`)
- Não use `isinstance(result, str)` como controle de fluxo — use exceções
- Não salve dados permanentes em `/tmp` — use `MEDIA_ROOT`
- Não delete o ZIP após extração — ele é a fonte da verdade
- Não ignore erros silenciosamente — logue com nível WARNING ou ERROR
- Não use `Any` ou acesso dinâmico de atributos (`getattr` pra campos conhecidos)

---

## Documentação interativa da API (Swagger / Redoc)

A documentação da API é **gerada automaticamente** a partir do código (rotas,
serializers e respostas), então fica sempre em sincronia com a implementação.
Com o servidor rodando, acesse:

| Recurso | URL | Descrição |
| --- | --- | --- |
| **Swagger UI** | `/api/docs/` | Documentação interativa — permite testar as rotas e ver request/response no navegador |
| **Redoc** | `/api/redoc/` | Documentação em formato de leitura |
| **Schema OpenAPI** | `/api/schema/` | Arquivo OpenAPI 3 (YAML) bruto |

Para autenticar no Swagger UI: faça login em `POST /accounts/login/`, copie o
`access` token retornado, clique em **Authorize** e informe `Bearer <access>`.

Você também pode exportar o schema como arquivo:

```bash
python manage.py spectacular --file schema.yml
```

---

## Visão geral das rotas

> A lista abaixo é um mapa rápido. Para os **schemas completos de request e
> response**, use a [documentação interativa](#documentação-interativa-da-api-swagger--redoc)
> em `/api/docs/`.

### Autenticação e contas — prefixo `/accounts/`

| Método | Rota | Descrição |
| --- | --- | --- |
| `POST` | `/accounts/login/` | Login, retorna os tokens JWT (`access` / `refresh`) |
| `POST` | `/accounts/refresh/` | Renova o `access` token |
| `GET/POST` | `/accounts/users/` | Lista / cria usuários |
| `GET/PUT/PATCH/DELETE` | `/accounts/users/<id>/` | Detalha / atualiza / remove usuário |
| `GET` | `/accounts/users/me/` | Dados do usuário autenticado |
| `GET` | `/accounts/users/me/memberships/` | Vínculos (memberships) do usuário autenticado |
| `POST` | `/accounts/users/me/password/` | Altera a senha do usuário autenticado |
| `GET/POST` | `/accounts/organizations/` | Lista / cria organizações |
| `GET/PUT/PATCH/DELETE` | `/accounts/organizations/<id>/` | Detalha / atualiza / remove organização |
| `GET/POST` | `/accounts/organizations/<id>/memberships/` | Membros da organização |
| `GET/PUT/PATCH/DELETE` | `/accounts/organizations/<id>/memberships/<id>/` | Detalhe de um membro |
| `GET/POST` | `/accounts/organizations/<id>/invites/` | Lista / cria convites |
| `GET/PUT/PATCH/DELETE` | `/accounts/organizations/<id>/invites/<id>/` | Detalhe de um convite |
| `POST` | `/accounts/invites/accept/<token>/` | Aceita um convite via token |
| `GET/POST` | `/accounts/roles/` | Lista / cria papéis (roles) |
| `GET/PUT/PATCH/DELETE` | `/accounts/roles/<id>/` | Detalhe de um papel |

### Experimentos / arquivos FCS — prefixo `/experiment/`

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET` | `/experiment/` | Lista experimentos (`?include_inactive=true` inclui inativados) |
| `POST` | `/experiment/init/` | Inicia um experimento e o upload em chunks |
| `POST` | `/experiment/upload-chunk/` | Envia um chunk do arquivo |
| `POST` | `/experiment/complete/` | Finaliza o upload e processa o ZIP |
| `POST` | `/experiment/check-hash/` | Dedup: informa se o SHA-256 já existe |
| `GET/PATCH/DELETE` | `/experiment/<experiment_id>/` | Detalha / edita / **inativa** um experimento |
| `POST` | `/experiment/<experiment_id>/copy` | Copia a análise para outro contexto |
| `GET` | `/experiment/<experiment_id>/download` | ZIP reconstruído por subsample |
| `POST` | `/experiment/<experiment_id>/files/init` | Inicia upload anexado ao experimento |
| `POST` | `/experiment/files/upload-chunk/` | Chunk do upload de arquivo |
| `POST` | `/experiment/files/complete/` | Finaliza upload anexado e extrai amostras |
| `GET` | `/experiment/<experiment_id>/subsamples/` | Lista/cria subsamples |
| `PATCH/DELETE` | `/experiment/<experiment_id>/subsamples/<id>/` | Renomeia / inativa subsample |
| `GET` | `/experiment/list/data/<experiment_id>/` | Lista os arquivos de um experimento |
| `GET` | `/experiment/file/<file_id>/list` | Lista os parâmetros de um arquivo |
| `GET` | `/experiment/file/<file_id>/density` | Heatmap/scatter da amostra |
| `GET` | `/experiment/file/<file_id>/stats` | Estatísticas por canal |
| `GET` | `/experiment/file/<file_id>/headers` | Metadados do header FCS |
| `POST` | `/experiment/file/<file_id>/process` | Reprocessa o upload do arquivo |
| `POST` | `/experiment/file/<file_id>/recompute` | Regenera o Parquet a partir do ZIP |
| `POST` | `/experiment/file/<file_id>/disable` | Inativa (freezer) a amostra |
| `POST` | `/experiment/file/<file_id>/enable` | Reativa a amostra |
| `PATCH` | `/experiment/file/<file_id>/subsample` | Move a amostra entre subsamples |

### Análise (gates) — prefixo `/analytics/`

| Método | Rota | Descrição |
| --- | --- | --- |
| `POST` | `/analytics/gate` | Cria gate |
| `PATCH/DELETE` | `/analytics/gate/<gate_id>` | Renomeia/edita / remove gate |
| `POST` | `/analytics/gate/apply` | Copia gates para outras amostras |
| `POST` | `/analytics/gate/delete-batch` | Exclui gates com escopo explícito |
| `GET` | `/analytics/gate/<gate_id>/list` | Dados filtrados por um gate |
| `GET` | `/analytics/gate/<gate_id>/density` | Heatmap/scatter do gate |

---

## Estrutura do projeto

```
.
├── accounts/            # autenticação, usuários, organizações, convites e papéis
├── analytics/           # gates, análises estatísticas e density/heatmaps
│   ├── models.py        #   GateModel, DashboardModel, AnalysisResult
│   ├── views.py         #   CRUD de gates, density por gate, dados filtrados
│   ├── permissions.py   #   escopo de gates (gates_visible_to)
│   └── tasks.py         #   recalculate_gate_analysis (síncrono)
├── fcs_parser/          # upload (em chunks) e processamento de arquivos FCS
│   ├── models.py        #   ExperimentModel, FileModel, FileDataModel
│   ├── views.py         #   init/chunk/complete upload, density, stats
│   ├── permissions.py   #   escopo de experimento/amostra/upload
│   ├── tasks.py         #   cleanup de parquet (síncrono)
│   ├── management/commands/  # cleanup_parquet, repair_source_path
│   └── services/        #   lógica de negócio (pipeline unificado)
│       ├── process_fcs.py              # parse .fcs → FCSResult
│       ├── process_experiment_file.py  # ZIP → extract → parse → FileData
│       ├── copy_experiment.py          # clonar experimento entre contextos
│       ├── decompressor.py             # extração de arquivos comprimidos
│       └── header_parser.py            # serialização de headers FCS
├── utils/               # utilitários compartilhados
│   ├── density.py       #   density engine (heatmap, scatter, histograma)
│   ├── mixins.py        #   SerializerByMethodMixin
│   └── validators.py    #   validação de ZIP/extensão
├── pandora/           # configuração do projeto Django (settings, urls)
├── docs/                # documentação adicional (ver server_config.md)
├── docker-compose.yml / docker-compose.prod.yml
└── requirements.txt
```

---

## Deploy

O processo de deploy (CI/CD via GitHub Actions + Docker Compose no servidor)
está documentado em [`docs/server_config.md`](docs/server_config.md).
