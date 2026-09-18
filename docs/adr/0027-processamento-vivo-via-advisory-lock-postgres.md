# ADR-0027 — Vivacidade de processamento via advisory lock do Postgres

- **Status:** Proposto
- **Data:** 2026-09-18
- **Contexto do código:** `fcs_parser/views.py` (`ExperimentCompleteView`, `ProcessFileDataView`), `fcs_parser/models.py` (`status`, `error_info`), `fcs_parser/services/process_experiment_file.py`

## Contexto

Processamento de experimento roda síncrono dentro do request (decisão
anterior: sem Celery/broker). Isso cria um modo de falha silencioso:
se o request morre no meio (restart, deploy, worker killed), o
experimento fica `status="processing"` para sempre — o flag no banco
diz "processando" mas ninguém processa.

A pergunta central: **como saber deterministicamente se um
`processing` está vivo?**

## Opções consideradas

1. **Boot sweep** (`processing → stopped` na subida): funciona porque
   restart mata todo request em voo — mas só com **1 réplica**. Com 2+
   workers, o boot de A marcaria `stopped` trabalho que B processa de
   verdade. Determinismo que depende de topologia é fragilidade
   adiada.
2. **Heartbeat + lease**: worker atualiza `heartbeat_at` a cada N
   segundos; `processing` com heartbeat velho = morto. Funciona com N
   réplicas, mas é **heurística** — TTL curto mata job legítimo lento,
   TTL longo atrasa a retomada — e exige thread escritora durante o
   processamento.
3. **Advisory lock do Postgres** (`pg_try_advisory_lock`): o lock é
   uma sessão do banco; morte do processo fecha o socket e o lock é
   liberado na hora, automaticamente. "Está processando?" vira uma
   consulta com resposta binária exata — e quem descobre o órfão já sai
   dono do lock, fazendo claim e probe na mesma operação atômica.

## Decisão

**Opção 3.** O processamento segura `pg_advisory_lock` (ou
`pg_advisory_xact_lock` dentro de `transaction.atomic()`) por
experimento durante toda a execução.

- Probe/retomada: `pg_try_advisory_lock` → `false` = vivo (responde
  202/409); `true` = órfão → quem perguntou já detém o lock e pode
  reprocessar sem janela de corrida.
- Nada de heartbeat, TTL, sweep de boot ou thread extra.
- Retry interno (até 3 tentativas com backoff) cobre falhas
  transitórias; falha final grava `error_info.error_message` para o
  front exibir (FE-33).

## Consequências

- O request de processamento segura uma conexão do pool durante toda a
  execução — já é o caso hoje, sem custo novo.
- Obrigatório `unlock` em `finally` (ou variante `xact`) porque
  `CONN_MAX_AGE` reusa conexões — lock vazado travaria retomadas
  futuras.
- Namespace de chaves: advisory locks são bigint — usar chave
  namespaced (`hashtext('experiment:' || id)`) para não colidir com
  outros usos futuros.
- Amarra ainda mais ao Postgres — já irreversível (`ArrayField`).
- Continua sem broker: se volume exigir fila real, a conversa é outro
  ADR (BE-27 é o caminho registrado).
