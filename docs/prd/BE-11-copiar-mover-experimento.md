# BE-11 — Copiar e mover experimento entre contextos (pessoal ↔ organização)

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `chore/ai-setup`
**Status:** Proposto. Depende de
[BE-10](BE-10-identidade-de-conteudo-e-blob-compartilhado.md) (blob
compartilhado). UI no front: FE-15.

## Problema

O usuário não consegue levar um experimento de um contexto para outro:
terminou a análise pessoal e quer levar pra organização, ou quer partir do
experimento de um colega sem re-upload. Sem identidade de conteúdo, a única
saída era subir o ZIP de novo — bytes e trabalho duplicados.

## Escopo

Duas operações distintas — copiar ≠ mover:

### 1. Copiar — análise independente sobre os mesmos arquivos

```
POST /experiment/<id>/copy
Body: { "title"?, "organization_id": <id> | null }   # null = pessoal

201 { "experiment_id": <novo_id> }
403 sem permissão na origem ou na org destino
```

- Novas linhas de `FileDataModel`/`Subsample`/`Gate` (estado inicial copiado)
  apontando pro mesmo `FileModel` — zero bytes duplicados.
- A cópia é independente: editar gates/subsamples de uma não toca a outra.
- Permissão: membro da org destino (ou pessoal = próprio usuário); leitura na
  origem basta para copiar? — **fechar na revisão** (copiar pra fora da org
  vaza dados se o usuário só é viewer).

### 2. Mover — o experimento troca de contexto, sem cópia

```
PATCH /experiment/<id>/
Body: { "organization_id": <id> | null }
```

- Reusa o endpoint de edição (BE-02) — move sem duplicar nada: mesmas
  linhas, mesmo `FileModel`, mesma análise.
- Regra: só dono/admin da origem **e** membro da org destino; mover pra org
  muda quem vê/edita (rever `can_edit_experiment`).

### Regras comuns

- Título duplicado no destino já é pego pelos constraints
  `unique_title_per_user_and_org` / `_personal` — traduzir 400 com mensagem.
- Cópia e movimento são atômicos (`transaction.atomic`): ou tudo ou nada.

## Arquivos a tocar

- `fcs_parser/views.py` + `urls.py` — `ExperimentCopyView`; `organization_id`
  no PATCH existente
- `fcs_parser/serializers.py` — payload de copy/move + validação de permissão
- `fcs_parser/services/` — rotina de cópia (clona rows, reusa blob)

## Critérios de aceite

- [ ] Copiar cria experimento independente apontando pro mesmo blob — storage
      não cresce.
- [ ] Mover muda `organization` sem duplicar linhas nem bytes.
- [ ] Sem permissão na origem/destino → 403; título duplicado no destino → 400.
- [ ] Cópia preserva subsamples, gates e seus `content_guid`s.

## Fora de escopo

- Compartilhar análise/gates entre cópias depois de criadas (são
  independentes por decisão).
- UI — FE-15 no pandora-front.
