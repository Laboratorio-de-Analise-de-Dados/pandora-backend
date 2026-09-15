# Documentação do Pandora (backend)

- `prd/` — o **que** cada entrega faz: escopo, contrato de API, arquivos tocados e
  critérios de aceite. Um PRD por MR.
- `adr/` — **por que** o sistema é assim: cada ADR registra uma decisão
  arquitetural, as alternativas que foram descartadas e as consequências
  (inclusive as ruins) de ter escolhido esse caminho.
- `server_config.md` — configuração do servidor de produção.

Os PRDs e ADRs do front vivem em `pandora-front/docs/`, com numeração própria e
independente desta. Decisão de produto/domínio (o que o sistema garante) fica
aqui; decisão de UI/arquitetura de front fica lá, com link cruzado por nome
(`pandora-backend/docs/adr/0006`) — nunca duplicada, para não divergirem.

## Convenções

- Um MR por PRD, título no padrão `feat(escopo): ...` / `fix(escopo): ...`.
- Base branch: `main`.
- `black` no código tocado; endpoints documentados com `@extend_schema`
  (drf-spectacular); permissão explícita (`IsAuthenticated`) e escopo por
  organização/criador.
- Validação de payload em serializer, não inline na view (ADR-0009).

## Como escrever um ADR

Copie `adr/TEMPLATE.md`, numere na sequência e **não edite ADRs aceitos**: uma
decisão que muda ganha um ADR novo com status `Substitui ADR-XXXX`, e o antigo
passa a `Substituído por ADR-YYYY`. O histórico das decisões erradas é o que dá
valor à pasta.

## Índice de ADRs

| ADR | Decisão | Status |
|---|---|---|
| [0001](adr/0001-soft-delete-de-amostras.md) | Amostra desabilitada (freezer), nunca apagada | Aceito |
| [0002](adr/0002-escopo-explicito-de-propagacao.md) | Propagação de gate só com escopo explícito do usuário | Aceito |
| [0003](adr/0003-linhagem-de-gates-por-copied-from.md) | Grupo de gates é linhagem (`copied_from`), não nome | Aceito |
| [0004](adr/0004-zip-como-fonte-de-verdade-parquet-como-cache.md) | ZIP é fonte de verdade; Parquet é cache regenerável | Aceito |
| [0005](adr/0005-api-nunca-deleta.md) | A API nunca deleta: inativa | Aceito |
| [0006](adr/0006-identidade-de-amostra-e-subsample.md) | Identidade da amostra é o caminho no ZIP; subsample em tabela | Aceito |
| [0007](adr/0007-nomes-hierarquicos-de-gates.md) | Nome de gate filho é hierárquico (`P1.1`) | Aceito |
| [0008](adr/0008-historico-append-only-de-analise.md) | Histórico de análise append-only por evento | Proposto |
| [0009](adr/0009-validacao-em-serializers.md) | Validação em serializer, não na view | Aceito |
| [0010](adr/0010-convites-sem-link-e-aceite-autenticado.md) | Convite sem link no e-mail; aceite autenticado | Aceito |
| [0011](adr/0011-api-de-arquivo-magra.md) | API de arquivo magra: headers crus, lote orquestrado no cliente | Aceito |
| [0012](adr/0012-identidade-de-conteudo-por-guid-e-sha256.md) | Identidade de conteúdo: `guid` por amostra, `sha256` por blob | Proposto |
| [0013](adr/0013-blob-compartilhado-entre-experimentos.md) | Blob físico compartilhado; experimento↔blob via `FileDataModel` | Proposto |
| [0014](adr/0014-todo-lookup-via-queryset-escopada.md) | Todo lookup por id passa por queryset escopado | Aceito |
| [0015](adr/0015-unicidade-de-titulo-entre-ativos.md) | Unicidade de título vale apenas entre experimentos ativos | Aceito |
| [0016](adr/0016-gate-nao-avaliavel-sem-canal.md) | Canal ausente torna o gate não-avaliável e corta a linhagem na amostra | Aceito |
