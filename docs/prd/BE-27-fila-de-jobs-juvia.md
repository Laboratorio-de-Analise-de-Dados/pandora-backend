# BE-27 — Fila de jobs de análise para o Juvia

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/juvia-job-queue`
**Status:** não iniciado — depende do juvia ADR-0002 (Proposto).

## Problema

Clustering é job pesado e esporádico. HTTP síncrono segura a request do
front sem status, sem retry e sem isolamento de falha. O usuário precisa
ver "na fila → processando → concluído/erro" com UI responsiva.

## Escopo

### 1. Tabela `analysis_jobs`

- `status`: `pending → processing → done | error → quarantine`
- refs: experimento, arquivo/subsample, modelo + params, payload_ref
- `attempts`, `last_error`, timestamps de transição

### 2. Endpoints internos (rede interna, não de usuário)

> O Juvia não é publicado — só existe na `pandora_net` (juvia ADR-0001).
> Esses endpoints são internos por topologia; token de serviço é defesa
> em profundidade opcional, não requisito.

- `POST /internal/jobs/claim` — claim atômico via
  `SELECT ... FOR UPDATE SKIP LOCKED`; devolve job + dados de entrada;
  N réplicas do Juvia nunca pegam o mesmo item
- `POST /internal/jobs/{id}/complete` — grava resultado, cria gate(s) e
  checkpoint `source=juvia` (ADR-0022)
- `POST /internal/jobs/{id}/fail` — incrementa `attempts`; após N falhas
  → `quarantine` (revisão manual, não descarte)

### 3. Status para o front

- `GET /analysis-jobs/{id}` (ou status embutido no experimento) — front
  faz polling com TanStack Query (`refetchInterval` enquanto ativo);
  WebSocket/SSE fica fora por ora

### 4. Cleanup

- `management command` que remove jobs `done` antigos (retenção
  configurável) — agendado via cron do host

## Arquivos a tocar

- `fcs_parser/models.py` ou app novo — `analysis_jobs` + migration
- endpoints internos (rota não exposta no nginx; token opcional)
- `fcs_parser/services/` — criação de gate + checkpoint no `complete`
- `management/commands/` — cleanup

## Critérios de aceite

- [ ] Claim atômico: 2 workers concorrentes nunca pegam o mesmo job
- [ ] Falha retenta até N vezes e cai em quarentena
- [ ] `complete` cria gate + checkpoint com origem juvia
- [ ] Front consegue exibir status via polling
- [ ] Cleanup remove jobs antigos

## Fora de escopo

- Broker dedicado (Redis/RabbitMQ) — Postgres resolve nessa escala
- WebSocket/SSE para status — polling cobre o volume atual
- O worker loop do Juvia — é do repo dele
