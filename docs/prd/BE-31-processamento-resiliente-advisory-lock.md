# BE-31 — Processamento resiliente: advisory lock, retomada e erro visível

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/processing-resilience`
**Status:** não iniciado.
**ADR:** `docs/adr/0027` · **Front relacionado:** `pandora-front` PRD FE-33

## Problema

O processamento de um experimento é síncrono dentro do request de
`complete/` (ADR do pipeline: sem Celery). Se o request morre no meio —
restart de deploy, autoreload em dev, worker killed, queda de rede — o
experimento fica `status="processing"` **para sempre**: nada volta a
processar, nada marca erro, e o front exibe "Processando" eterno.

Observado em dev (2026-09): o autoreload do Django reiniciou o worker
entre o último `upload-chunk` e o `complete`; o experimento ficou
preso em `processing` e a recriação com o mesmo nome retornou 400 pela
constraint de título.

Falhas reais já viram `status="error"` com `error_info`, mas:

- não há retry — uma falha transitória é terminal;
- o caso órfão (request morto) nunca vira `error` — fica `processing`;
- `complete/` e `process/` rejeitam experimento `processing` com 400,
  ou seja **o estado que precisa de retomada é justamente o bloqueado**.

## Decisão (ADR-0027)

O mecanismo de verdade é o **advisory lock do Postgres**
(`pg_try_advisory_lock`), não flags nem sweeps:

- O worker segura um advisory lock por experimento durante todo o
  processamento. O lock mora na sessão do banco — **morte do processo
  fecha o socket e o Postgres libera o lock na hora**, sem timeout,
  heartbeat ou sweep.
- "Está processando de verdade?" vira uma pergunta determinística:
  `pg_try_advisory_lock` falha ⟺ alguém processa agora; sucesso ⟺
  órfão, e quem perguntou já sai dono do lock (claim atômico —
  impossível duas réplicas processarem juntas).
- Funciona com N réplicas/workers porque o estado vive no Postgres,
  não em memória de processo.

## Escopo

### 1. Claim por advisory lock no processamento

- `ExperimentCompleteView` e `ProcessFileDataView` (e qualquer entry
  point que processe) abrem com
  `pg_try_advisory_lock(<chave derivada do experiment.id>)`:
  - **lock obtido** → segue o processamento; `finally` libera
    (`pg_advisory_unlock`) ou usar `pg_advisory_xact_lock` dentro de
    `transaction.atomic()` para liberação automática em
    commit/rollback/crash.
  - **lock não obtido** → 409/`202` "já está em processamento" (não
    500; é condição de corrida normal).
- Chave do lock: usar `hashtext`/`hashtextextended` sobre
  `f"experiment:{id}"` ou o id direto com namespace — documentar a
  escolha para não colidir com outros usos futuros de advisory lock.

### 2. Retomada (o caso órfão)

- `complete/` deixa de rejeitar `status="processing"`: ao receber um
  `processing`, primeiro tenta o lock —
  - pega o lock → era órfão → **reprocessa** (o pipeline é idempotente:
    ZIP é fonte da verdade, amostras são deduplicadas);
  - não pega → responde "em processamento" (202).
- Novo status não é estritamente necessário: "processing órfão" =
  `processing` sem lock. Se quisermos materializar para o front
  distinguir visualmente, avaliar `stopped` no serializer via probe —
  **decidir na implementação**; o front só precisa de um jeito
  confiável de saber que pode reenviar.

### 3. Retry interno e erro com motivo

- O corpo de processamento roda em loop de até **3 tentativas** com
  backoff curto (ex.: 1s, 2s, 4s) para falhas transitórias (IO, disco
  cheio temporário, lock de arquivo). Falha definitiva → `error`.
- Persistir motivo: `error_info` já existe — padronizar
  `{"error_message": str(e), "details": traceback, "attempts": n,
  "failed_at": iso}` e **serializar `error_info.error_message` na
  resposta de detalhe/listagem** (o front mostra no hover — FE-33).
- `processing_attempts`/`processing_heartbeat` **não** são necessários:
  a vivacidade vem do lock, não de timestamps.

### 4. Sem sweep de boot

Não criar job na subida marcando `processing → stopped`: com o lock, o
sweep é redundante — o próximo `complete`/probe resolve o órfão na
hora. (O sweep era a alternativa single-réplica; descartado porque
quebraria com 2 réplicas — ADR-0027.)

### 5. Probe opcional

Se o front precisar perguntar sem disparar retomada: endpoint ou campo
calculado `processing_alive: bool` no serializer do experimento que
faz `pg_try_advisory_lock` + unlock imediato. Barato e determinístico.
Incluir apenas se o FE-33 precisar — caso contrário o `complete/`
idempotente resolve.

## Fora de escopo

- Fila/broker (Celery/Redis) — permanece a decisão de processamento
  síncrono; BE-27 (fila Juvia) é caminho separado se volume exigir.
- Retry infinito ou reprocessamento automático sem chamada — a
  retomada é sempre disparada pelo `complete/` re-chamado (front faz
  isso silenciosamente, FE-33).

## Arquivos a tocar

- `fcs_parser/views.py` — `ExperimentCompleteView`, `ProcessFileDataView`
- `fcs_parser/services/` — helper `experiment_processing_lock(id)`
  (context manager) + loop de retry ao redor de
  `process_experiment_zip`
- `fcs_parser/serializers.py` — expor `error_info.error_message`
- `fcs_parser/tests.py` — lock contention (409/202), retomada de
  órfão, retry exaure em erro real, `error_info` serializado

## Critérios de aceite

- [ ] Experimento `processing` órfão é retomado por um `complete/`
      posterior — sem intervenção manual
- [ ] Dois `complete/` concorrentes nunca processam o mesmo
      experimento duas vezes (lock)
- [ ] Falha real vira `status="error"` com `error_info.error_message`
      legível na API
- [ ] Retry interno: falha transitória recupera sem `error`; 3 falhas
      seguidas marcam `error` com o último motivo
- [ ] `python manage.py test` verde + testes novos cobrindo órfão,
      contenção e erro
