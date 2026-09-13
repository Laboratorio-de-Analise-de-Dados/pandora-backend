# ADR-0005 — A API nunca deleta: `DELETE` inativa

- **Status:** Aceito
- **Data:** 2026-09-11
- **Contexto do código:** `accounts/views.py` (`MembershipDetailView`), `fcs_parser/views.py` (`SubsampleDetailView`)

## Contexto

`DELETE /organizations/<id>/memberships/<pk>/` apagava o vínculo. Junto com ele
ia a resposta para "quem tinha acesso a este experimento quando a análise foi
feita?" — pergunta que um laboratório precisa responder depois, não antes.
A mesma regra reapareceu em subsamples.

## Decisão

Regra geral da API: **nunca se deleta, só se inativa.** `DELETE` é uma operação
de arquivamento — devolve 200/204, mas o registro permanece com
`status="inactive"` / `active=False`, fora das listagens por default e visível
com `include_inactive=true`. Reativar é recriar o mesmo recurso.

Corolários já implementados: o último admin ativo não pode ser removido nem
rebaixado; inativar um subsample desvincula suas amostras (`subsample=None`) em
vez de cascatear.

## Alternativas consideradas

### A) Hard delete (comportamento original)

Descartada: perde histórico de acesso e é irreversível em um domínio onde a
rastreabilidade é o produto.

### B) Manter `DELETE` como hard delete e expor um `POST /deactivate`

Descartada: dois caminhos, um deles destrutivo, e o destrutivo é justamente o
que um cliente HTTP qualquer chama por convenção. Preferimos que o verbo padrão
seja o seguro.

## Consequências

- Todo `GET` precisa filtrar inativos explicitamente; esquecer vira bug visível.
- Unicidade colide com registros inativos (ex.: nome de subsample de um
  arquivado). Onde isso aparecer, a constraint precisa ser condicionada a
  `active=True` ou a reativação precisa ser o caminho oferecido.
- Não existe expurgo. Um dia vai existir, e será uma rotina administrativa
  explícita, nunca um `DELETE` de API.
