# BE-16 — Título único apenas entre experimentos ativos

**Repo:** pandora-backend · **Tipo:** fix · **Base:** `main`
**Status:** implementado em `feat/scoped-lookups`. Decisão: ADR-0015.

## Problema

As constraints `unique_title_per_user_and_org` e
`unique_title_per_user_personal` valiam para **todos** os experimentos,
inclusive os arquivados (`active=False`). Caso observado:

1. Usuário tinha um experimento pessoal "exp" ativo e outro "exp" na
   organização.
2. Ao mover o da org para o pessoal → 400 (constraint) — correto, dois
   ativos colidiriam.
3. Desativou o pessoal e tentou mover de novo → **ainda 400**, porque o
   experimento arquivado continuava ocupando o título.

Como `active=False` tira o experimento de todas as listagens e buscas,
exigir título único sobre ele era regra sem benefício — e impedia um fluxo
legítimo de arquivar-o-antigo-e-reusar-o-nome.

## Regra

**Dois experimentos ativos nunca compartilham `(title, created_by,
organization)`.** Inativos não participam da unicidade.

## Mudanças

- `fcs_parser/models.py` — `condition=Q(..., active=True)` nas duas
  `UniqueConstraint` (índice parcial; migration `0015`).
- `ExperimentInitSerializer.validate` — checagens de duplicidade filtram
  `active=True` (consistente com o banco).
- `copy_experiment._unique_title` — sufixo `_2`, `_3`… só conta ativos;
  copiar sobre contexto com homônimo arquivado não gera sufixo.
- `ExperimentRestoreView` — reativar um experimento cujo título foi
  reocupado por um ativo agora colide na constraint nova; tratado como
  `409 Conflict` ("Já existe um experimento ativo com este título neste
  contexto."), não 500.

## Testes

- `test_move_permite_mesmo_titulo_de_experimento_inativo` — move org →
  pessoal com homônimo arquivado: 200.
- `test_move_rejeita_mesmo_titulo_de_experimento_ativo` — homônimo ativo:
  400 e experimento permanece na org.
- `test_restore_com_titulo_ativo_duplicado_retorna_409` — restore sobre
  título reocupado: 409, permanece inativo.
