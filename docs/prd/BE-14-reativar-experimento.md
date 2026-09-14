# BE-14 — Reativar experimento (caminho de volta do soft-delete)

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Status:** implementado na `main` (working tree — aguarda commit junto ao
BE-13 em andamento). UI no front: FE-21. Decisão de domínio: ADR-0005.

## Problema

O `DELETE /experiment/<id>/` é arquivamento desde o ADR-0005: seta
`active = False`, os dados permanecem e o experimento sai das listagens
(`experiments_visible_to` filtra `active`). Mas não existia caminho de
volta — uma vez desativado, o experimento era inacessível até para o dono
(o queryset escopado devolve 404 em GET/PATCH/DELETE). Desativar virava,
na prática, um delete irreversível para o usuário.

## Escopo

```
POST /experiment/<id>/restore

200 ListExperimentSerializer do experimento reativado
403 quem não é dono nem admin na origem
404 quem não enxerga o experimento (outsider)
```

- Reativa `active = True`; idempotente — chamar em experimento já ativo
  devolve 200 sem efeito colateral.
- **Permissão:** a mesma da inativação — `can_move_experiment` (dono,
  super admin ou `org_admin` na organização de origem). Reativar muda o
  que os outros veem, então não basta ser membro/viewer.
- O lookup usa `experiments_visible_to(user, include_inactive=True)` —
  exceção deliberada ao padrão "inativos são invisíveis", porque é o
  único caminho de volta.
- Nada além do flag muda: amostras, gates, subsamples e blobs ficam como
  estavam (o soft-delete nunca tocou neles).

## Arquivos a tocar

- `fcs_parser/views.py` — `ExperimentRestoreView`
- `fcs_parser/urls.py` — `path("<int:experiment_id>/restore", ...)`
- `fcs_parser/tests.py` — `ExperimentRestoreTestCase`

## Critérios de aceite

- [x] Restore reativa e o experimento volta a aparecer em `GET
      /experiment/`
- [x] Idempotente em experimento já ativo (200)
- [x] Membro sem papel admin → 403; outsider → 404
- [x] Suíte `fcs_parser` verde (62 testes)

## Nota de implementação (estado da working tree)

Implementado, mas **não commitado** — está na working tree da `main`
misturado ao WIP do BE-13. Para quem retomar:

- `fcs_parser/views.py` — `ExperimentRestoreView` logo após
  `RetrieveDeleteExperimentView` (~linha 391). Usa
  `experiments_visible_to(user, include_inactive=True)` +
  `can_move_experiment`.
- `fcs_parser/urls.py` — `path("<int:experiment_id>/restore",
  ExperimentRestoreView.as_view())` registrado junto aos outros
  `<int:experiment_id>/…`.
- `fcs_parser/tests.py` — `ExperimentRestoreTestCase` no fim do arquivo
  (4 testes).

**Correções já aplicadas nesta working tree** (eram bugs do WIP BE-13,
não remover ao commitar):

- `views.py`: re-adicionado `can_edit_experiment` ao import de
  `permissions` — sem ele `ExperimentCopyView.post` (~linha 1117) quebra
  com `NameError`.
- `tests.py`: `test_init_requires_edit_permission` e
  `test_user_without_access_cannot_change_subsamples` ajustados de 403
  para **404** — outsiders não enxergam o experimento
  (`experiments_visible_to`), coerente com `ScopedAccessTestCase`.

## Fora de escopo

- Listagem de inativos com distinção visual e ação de reativar — FE-21.
- Delete físico/purge de blob — política de limpeza ainda não decidida.
- Auditoria de quem desativou/reativou — entra com o histórico (BE-08).
