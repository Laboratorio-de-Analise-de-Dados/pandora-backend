# ADR-0003 — O grupo de um gate é a linhagem (`copied_from`), não o nome

- **Status:** Aceito
- **Data:** 2026-09-03
- **Contexto do código:** `analytics/models.py` (`GateModel.copied_from`), `analytics/gate_scope.py`, `analytics/views.py` (PATCH de `gate_coordinates`)

## Contexto

"Todos os gates `P1`" era resolvido comparando nome (`name === "P1"`). Isso junta
coisas diferentes: um `P1` desenhado dentro de `P2` não é o mesmo `P1` da raiz, e
uma cópia que o usuário ajustou à mão para caber na população daquela amostra
deixou de ser "o mesmo gate" — mas continuava agrupada com as outras no
relatório e recebendo alterações em lote.

## Decisão

Aplicar um gate em outras amostras cria uma relação **1:N** explícita
(`copied_from` aponta para o original). Esse é o grupo, para propagação e para o
agrupamento do relatório.

No momento em que a **geometria** de uma cópia é editada só naquela amostra, o
backend limpa `copied_from`: a cópia passa a ser um gate **1:1** independente e
sai do grupo. Editar **nome/cor não desanexa** — nome e cor são propriedades da
linhagem, não da amostra; é por isso que só faz sentido renomear em grupo quem
ainda está atachado. Se o usuário escolher `scope="experiment"` no reshape, a
geometria é propagada e ninguém desanexa.

Gates legados (sem `copied_from` populado) caem no agrupamento por caminho de
nomes, senão o agrupamento simplesmente desapareceria para quem já tem dados.

## Alternativas consideradas

### A) Continuar agrupando por nome

Descartada: junta homônimos de ramos diferentes e não distingue a cópia
customizada da cópia fiel.

### B) Tabela de "grupo de gates" (M:N) em vez da FK de linhagem

Descartada por desproporção: a FK já existia, expressa a origem
("de quem eu sou cópia") e resolve o caso real. Uma tabela de grupo permitiria
reagrupar gates criados manualmente — cenário que ninguém pediu ainda.

### C) Flag `customizado` na cópia, mantendo o vínculo

Descartada: mantém o gate dentro do grupo para propagação (é o vínculo que a
propagação percorre), então o sintoma continuaria — só mudaria de nome.

## Consequências

- O agrupamento do relatório passa a refletir o que o usuário vê na tela.
- Gate criado **manualmente** em cada amostra (sem "aplicar") nunca entra em
  grupo — só nome/caminho o aproxima. Se isso incomodar, é o caso da alternativa
  (B).
- Desanexar é silencioso e hoje irreversível: o usuário não tem como "voltar
  para o grupo". Outro motivo para ADR-0008.
