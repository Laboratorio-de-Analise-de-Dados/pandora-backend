---
name: implementar
description: Implementar uma tarefa do board neste repo — TDD obrigatório, security gates (escopo/IDOR, soft delete, serializers), cuidado expand-contract com migrations e Trello atualizado
argument-hint: "<PRD/card: ex. BE-46>"
allowed-tools:
  - read
  - edit
  - grep
  - glob
  - exec
  - mcp_call_tool
  - mcp_list_tools
---

# Implementar — pandora-backend (TDD + security gates)

Implementação guiada por spec. O `AGENTS.md` é a referência operacional —
esta skill é o checklist de execução por cima dele.

## 1. Contexto antes de codar (não pule)

- **`.scratch/tasks-<slug>.md` primeiro**: o plano de implementação
  (tasks ordenadas, testes, notas de execução) mora local e gitignored
  neste repo — leia-o em vez de abrir o card no MCP. Se não existir,
  ele deveria ter sido gerado pelo `/to-tickets`: peça o PRD e derive.
- **PRD → ADRs**: o card/PRD (`pandora-docs/prd/BE-XX-*.md`) linka os
  ADRs (`docs/adr/XXXX-*` e os sensíveis em
  `pandora-docs/adr/backend/`). Leia antes de tocar código — o contrato
  está no PRD, o porquê está no ADR.
- **`docs/TRACKER.md`**: frente ativa na mesma área de arquivos =
  conflito — alinhe ou pegue outra. Registre sua linha no mesmo commit.
- **Par front/back**: se o PRD declara par FE-XX, confira o estado do
  irmão antes de fechar contrato de API.
- **Branch**: `git checkout main && git pull` → `git checkout -b
  feat|fix|refactor/<slug>`. Nunca commitar na `main`.
- **Card → 🔨 Em andamento** ao assumir (`/trello-board`).

## 2. TDD — ciclo obrigatório

1. **Escreva o teste que falha primeiro**: caso de aceite do PRD vira
   `tests` do app (ou `test_*.py` existente). Bug reportado → teste que
   reproduz o bug antes do patch.
2. Rode e veja falhar: `python manage.py test <app>.tests.<classe>`
   (precisa do Postgres — com compose: `DATABASE_HOST=localhost`).
3. Implemente o mínimo para ficar verde; rode o teste de novo.
4. Refatore com o teste verde; rode a suíte do app tocado.
5. Mexeu em permissão, migration, gate/estatística ou contrato de API →
   o teste de regressão faz parte da entrega, não é opcional.

## 3. Security gates — checar antes de abrir PR

- [ ] Todo lookup passa por queryset escopada —
      `get_object_or_404(experiments_visible_to(user), id=...)`.
      `Model.objects.get(id=...)` cru = IDOR + 500 em vez de 404.
- [ ] `permission_classes` explícita em toda view nova +
      `can_edit_experiment`/`can_move_experiment` para escrita.
- [ ] Validação de payload em **serializer**, nunca inline na view
      (ADR-0009); `inline_serializer` do spectacular é só doc.
- [ ] **A API nunca deleta** — soft delete `active=false` (ADR-0001/0005).
- [ ] Invariante no banco (`UniqueConstraint`/`CheckConstraint` em
      `Meta.constraints`); `IntegrityError` na borda vira 400.
- [ ] Erro de domínio → `serializers.ValidationError`/`Response` 4xx;
      exceção não tratada nunca é resposta (o erro 500 em HTML que o
      usuário vê é sempre dívida).
- [ ] Nenhum segredo hardcoded (`SECRET_KEY`, senhas, SMTP vêm de env).
- [ ] Endpoint novo documentado com `@extend_schema`.

## 4. Banco de dados — expand-contract

- Toda migration funciona com a **versão anterior do código** — é o que
  torna o rollback automático do release seguro.
- Nunca dropar coluna/tabela na mesma release que para de usá-la:
  release N expande, release N+1 contrai.
- `makemigrations` via compose: `docker compose exec web python
  manage.py makemigrations`. Dockerfile roda `--check` — model mudou
  sem migration = build quebra.
- Timestamps sempre UTC (`USE_TZ`/`TIME_ZONE` fixos); resposta em
  ISO-8601 `Z`.
- Dado de paciente/amostra nunca em teste commitado — fixture sintética.

## 5. Verificação local (portão antes de concluir)

```bash
python manage.py test        # suíte — DATABASE_HOST=localhost c/ compose
black <arquivos tocados>     # formatter oficial
python manage.py check       # sanity
```

## 6. Fechamento — .scratch/ marca o dia a dia, Trello só os marcos

- Marque as tasks feitas no `.scratch/tasks-<slug>.md` local (o arquivo
  é seu caderno de execução — MCP não precisa ser chamado a cada task).
- **Marcos Trello** (única vez que o MCP entra): assumir → 🔨 Em
  andamento; PR aberto → comentário com o link + ✅ nos critérios
  verificados do checklist "Acceptance Criteria"; merge na `main` →
  📦 Entregue + comentário (branch/PR); release deployada + smoke ok →
  🚀 Em produção.
- Bug/dívida achada no caminho → card novo no Backlog — Back, capa
  rosa, com referência ao PRD/PR.
