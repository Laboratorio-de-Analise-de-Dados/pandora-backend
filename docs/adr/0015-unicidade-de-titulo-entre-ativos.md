# ADR-0015 — Unicidade de título vale apenas entre experimentos ativos

- **Status:** Aceito
- **Data:** 2026-09-16
- **Contexto do código:** `fcs_parser/models.py` (Meta.constraints),
  `fcs_parser/serializers.py`, `fcs_parser/services/copy_experiment.py`,
  `fcs_parser/views.py` (`ExperimentRestoreView`)
- **Relaciona:** ADR-0001 (soft-delete), ADR-0005 (API nunca deleta),
  PRD BE-16

## Contexto

Desde o ADR-0005, `DELETE` é arquivamento: `active=False` remove o
experimento de todas as listagens e lookups (`experiments_visible_to`),
mas a linha permanece. As `UniqueConstraint` de título
(`unique_title_per_user_and_org`, `unique_title_per_user_personal`), porém,
não filtravam `active` — um experimento arquivado, invisível ao usuário,
continuava bloqueando o título em criar/renomear/mover/copiar.

Efeito observado: mover um experimento da organização para o espaço
pessoal falhava com 400 mesmo depois de arquivar o homônimo pessoal — a
constraint enxergava o que o usuário não enxerga mais.

## Decisão

As duas constraints passam a ser índices parciais sobre **ativos**:

```python
condition=models.Q(organization__isnull=False, active=True)   # org
condition=models.Q(organization__isnull=True, active=True)    # pessoal
```

E todo ponto do código que reproduz a regra filtra `active=True`
(serializer de init, `_unique_title` da cópia). A invariante fica:
**título único por `(created_by, organization)` entre experimentos
ativos; arquivados são livres.**

## Consequências

- Arquivar um experimento **libera seu título** — criar/mover/copiar um
  homônimo passa a ser permitido. É a semântica esperada do soft-delete:
  inativo não existe para o usuário.
- **Reativar** pode colidir: se o título foi reocupado por um ativo, o
  `save` estoura a constraint. O endpoint de restore trata como
  `409 Conflict` — reativar exige antes renomear ou arquivar o ocupante.
- Postgres suporta índice parcial nativamente; nenhum dado é migrado —
  experimentos inativos pré-existentes só deixam de participar do índice.
- Subsamples/amostras seguem com suas próprias constraints (que já
  consideram `active` onde faz sentido); esta decisão é específica ao
  título de experimento.
