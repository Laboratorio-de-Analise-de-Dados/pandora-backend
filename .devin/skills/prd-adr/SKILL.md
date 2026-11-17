---
name: prd-adr
description: Criar PRD (pandora-docs/prd/BE-XX) ou ADR (docs/adr/XXXX local, sensíveis em pandora-docs/adr/backend/) seguindo as convenções do backend
argument-hint: "[prd|adr] <tema da entrega ou decisão>"
allowed-tools:
  - read
  - edit
  - grep
  - glob
---

Documentar uma entrega (PRD) ou uma decisão arquitetural (ADR) neste repo.

Regra de bolso: PRD registra **o que** a entrega faz; ADR registra **por que**
uma decisão foi tomada. Se a mudança ainda não tem decisão fechada, o ADR vem
antes do PRD.

## Passo 0 — decidir o tipo

- **PRD** (`../pandora-docs/prd/BE-XX-*.md`): escopo, comportamento esperado,
  arquivos a tocar, critérios de aceite. Um PRD por MR.
- **ADR de domínio** (`docs/adr/XXXX-*.md`): modelo de dados, escopo de
  propagação, storage, convenção. Uma decisão por ADR.
- **ADR sensível** (auth, segurança, infra, storage de dado de paciente,
  pipeline de deploy) → `../pandora-docs/adr/backend/XXXX-*.md`, mantendo a
  numeração original do repo.
- Decisões de **UI/convenção do front** NÃO vão aqui — ficam em
  `pandora-front/docs/adr/` e são referenciadas por nome, nunca copiadas.

## PRD — procedimento

1. Descobrir o próximo número: `glob ../pandora-docs/prd/BE-*.md`, pegar o
   maior + 1.
2. Criar `../pandora-docs/prd/BE-XX-<slug-kebab>.md` em PT-BR, com o formato:

```markdown
# BE-XX — Título descritivo

**Repo:** pandora-backend · **Tipo:** feature|fix|refactor|chore · **Base:** `main`
**Branch sugerida:** `feat/<slug>`
**Status:** não iniciado | em andamento | entregue. Par de front: FE-XX.
Decisão de modelo: ADR-XXXX (repo).
(estado na criação — depois disso o status vive no card do Trello; não
edite o PRD para mudar status nem para marcar checklist)

## Problema

O problema concreto, com o comportamento observado hoje.

## Escopo

### 1. <parte>

- comportamento esperado, regras de negócio, edge cases

## Arquivos a tocar

- caminhos reais + o que muda em cada um

## Critérios de aceite

- checklist verificável EM TEXTO (sem `- [ ]` — checkbox mora só no card)

## Fora de escopo

- o que fica explicitamente de fora
```

3. Consultar `../pandora-docs/prd/BE-20-checkpoints-de-analise.md` como
   referência de tom e nível de detalhe.
4. Adicionar linha na tabela de `../pandora-docs/prd/README.md`.
5. **PRD commita direto na `main` do `pandora-docs`** (repo de docs é trunk
   puro, sem branch/PR); o código continua no PR deste repo, que referencia
   o PRD por nome.

## ADR — procedimento

1. Descobrir o próximo número: `glob docs/adr/*.md`, pegar o maior + 1
   (formato `000X`, 4 dígitos). ADR sensível usa a mesma sequência —
   número vem daqui, arquivo vai para `../pandora-docs/adr/backend/`.
2. Copiar a estrutura de `docs/adr/TEMPLATE.md` para
   `docs/adr/XXXX-<slug-kebab>.md`: Status, Data (AAAA-MM-DD), Contexto do
   código, Contexto (problema observado, não a solução), Decisão,
   Alternativas consideradas (A/B/C, cada uma com o motivo do descarte),
   Consequências (incluindo o que vira dívida).
3. **Nunca editar ADR aceito.** Se a decisão muda: ADR novo com
   `Substitui ADR-XXXX` e o antigo passa a `Substituído por ADR-YYYY` —
   atualizar os dois.
4. Adicionar linha na tabela "Índice de ADRs" de `docs/README.md`.

## Convenções de escrita

- PT-BR, direto, sem enfeite. Problema antes de solução; comportamento
  observado, não intenção.
- Referenciar arquivos/ADRs pelo caminho real
  (`analytics/services/`, `fcs_parser/permissions.py`), nunca descrever de
  memória — conferir com `grep`/`glob` antes.
- Status do ADR novo: `Aceito` se a decisão já vigora no código, `Proposto`
  se ainda está em discussão.
