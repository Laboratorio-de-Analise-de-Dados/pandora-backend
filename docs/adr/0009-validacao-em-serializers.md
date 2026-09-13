# ADR-0009 — Validação de payload em serializer, não na view

- **Status:** Aceito
- **Data:** 2026-09-01
- **Contexto do código:** `fcs_parser/serializers.py`, `analytics/serializers.py`

## Contexto

Views antigas (ex.: `ExperimentInitView`) validam campos lendo `request.data`
direto, com `if`s e mensagens de erro montadas à mão. O resultado é formato de
erro inconsistente entre endpoints, schema do drf-spectacular incompleto e
regra duplicada quando o mesmo campo aparece em dois lugares.

## Decisão

Todo endpoint novo valida com serializer — `ModelSerializer` para recursos,
serializer de request (ou `inline_serializer`) para ações. As views cuidam de
autorização e orquestração. As validações inline existentes ficam registradas
como dívida e serão migradas em um MR de refactor próprio, não junto com
feature.

## Alternativas consideradas

### A) Manter validação nas views

Descartada: é a origem da inconsistência de erro que o front tem que tratar
caso a caso.

### B) Refatorar tudo agora, junto com as features

Descartada: mistura mudança de comportamento com refactor no mesmo diff e
dificulta a revisão — exatamente o que este projeto está tentando evitar
(MRs pequenos e revisáveis).

## Consequências

- Erros padronizados (`{campo: [mensagem]}`) e schema correto.
- Enquanto o refactor não acontece, coexistem dois estilos no código; o PRD do
  refactor é o lugar de rastrear isso.
