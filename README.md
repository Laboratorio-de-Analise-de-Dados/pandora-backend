# Pandora Backend (citosharp)

Backend da plataforma **Pandora** para análise de dados de **citometria de fluxo**.
Permite autenticação via JWT, gestão de organizações/usuários, upload e
processamento de arquivos **FCS** (em chunks) e criação/consulta de *gates* de
análise.

> Este repositório é a API consumida pelo
> [`pandora-front`](https://github.com/Laboratorio-de-Analise-de-Dados/pandora-front).

---

## Sumário

- [Stack](#stack)
- [Documentação interativa da API (Swagger / Redoc)](#documentação-interativa-da-api-swagger--redoc)
- [Como rodar o projeto](#como-rodar-o-projeto)
  - [Com Docker (recomendado)](#com-docker-recomendado)
  - [Localmente sem Docker](#localmente-sem-docker)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Visão geral das rotas](#visão-geral-das-rotas)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Deploy](#deploy)

---

## Stack

- **Python 3.13** / **Django 5.2** + **Django REST Framework**
- **JWT** (`djangorestframework-simplejwt`) para autenticação
- **PostgreSQL** como banco de dados
- **Celery** + **Redis** para processamento assíncrono dos arquivos FCS
- **drf-spectacular** para geração automática da documentação OpenAPI
- **Docker** / **docker-compose** para ambiente local e produção

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

## Como rodar o projeto

### Com Docker (recomendado)

Sobe a API, o banco PostgreSQL, o Redis e o worker do Celery de uma vez.

```bash
# 1. crie a rede externa usada pelo compose (apenas na primeira vez)
docker network create pandora_net

# 2. suba os serviços
docker compose up --build
```

A API ficará disponível em `http://localhost:8085` (mapeada para a porta 8000 do
container). A porta pode ser alterada com a variável `WEB_PORT`.

> Os valores de banco/redis para o ambiente de desenvolvimento já vêm definidos
> no `docker-compose.yml`.

### Localmente sem Docker

Pré-requisitos: Python 3.13, uma instância de PostgreSQL e (para o
processamento assíncrono) um Redis.

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

Para rodar o worker do Celery (necessário para processar os arquivos FCS):

```bash
celery -A citosharp worker -l info
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
| `REDIS_HOST` | Host do Redis (padrão `redis`) |
| `REDIS_PORT` | Porta do Redis (padrão `6379`) |
| `MEDIA_ROOT` | Diretório onde os uploads são salvos |
| `EMAIL_HOST_USER` | Usuário SMTP (envio de convites por e-mail) |
| `EMAIL_HOST_PASSWORD` | Senha SMTP |
| `TEST` | Se definida, usa SQLite em vez do PostgreSQL (útil para testes) |

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
| `GET` | `/experiment/` | Lista experimentos |
| `POST` | `/experiment/init/` | Inicia um experimento e o upload em chunks |
| `POST` | `/experiment/upload-chunk/` | Envia um chunk do arquivo |
| `POST` | `/experiment/complete/` | Finaliza o upload e dispara o processamento |
| `GET/DELETE` | `/experiment/<experiment_id>/` | Detalha / remove um experimento |
| `GET` | `/experiment/list/data/<experiment_id>/` | Lista os arquivos de um experimento |
| `GET` | `/experiment/file/<file_id>/list` | Lista os parâmetros de um arquivo |
| `POST` | `/experiment/file/<file_id>/process` | Processa os dados de um arquivo |

### Análise (gates) — prefixo `/analytics/`

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET/POST` | `/analytics/gate` | Lista / cria gates |
| `GET` | `/analytics/gate/<gate_id>/list` | Lista os dados de um gate |

---

## Estrutura do projeto

```
.
├── accounts/        # autenticação, usuários, organizações, convites e papéis
├── analytics/       # gates e análises
├── fcs_parser/      # upload (em chunks) e processamento de arquivos FCS
├── citosharp/       # configuração do projeto Django (settings, urls, celery)
├── utils/           # utilitários compartilhados (mixins etc.)
├── docs/            # documentação adicional (ver server_config.md)
├── docker-compose.yml / docker-compose.prod.yml
└── requirements.txt
```

---

## Deploy

O processo de deploy (CI/CD via GitHub Actions + Docker Compose no servidor)
está documentado em [`docs/server_config.md`](docs/server_config.md).
