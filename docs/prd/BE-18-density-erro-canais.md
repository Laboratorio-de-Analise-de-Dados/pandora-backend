# BE-18 — "Erro ao carregar dados" ao abrir gate em amostra sem o canal

**Repo:** pandora-backend · **Item do doc:** teste 14/09/2026, item 2 · **Tipo:** fix · **Base:** `main`
**Branch sugerida:** `fix/gate-density-missing-channel`
**Status:** implementado em `fix/gate-density-missing-channel`.

## Problema

Relato do teste (áudio + print): estratégia de gate FSC-A × SSC-A → quadrantes
FITC-A × APC-A aplicada em várias amostras do experimento "90% K562 10% PBMC
Mixture". Aplicar funcionou; ao **abrir o gate** em algumas amostras
(`ES488837.fcs`, e possivelmente `ES488833.fcs`) o plot mostra
"Erro ao carregar dados". No print, a amostra renderiza FSC × SSC no nível do
arquivo mas falha ao pedir `FITC-A × APC-A` dentro de `P1`.

`GateDensityView.get` devolve `400 {"detail": "Columns 'x' or 'y' not found in
dataset."}` sempre que `compute_density`/`subsample_scatter`/`compute_histogram`
retornam `None` — o que cobre **dois casos distintos** tratados como um só:

1. **Canal ausente na amostra** — o arquivo não tem `fitc_a`/`apc_a` (nomes de
   canal variam entre aquisições/lotes). É a hipótese mais provável do relato
   ("talvez sejam esses arquivos especificamente").
2. **Dataset vazio após o filtro** — `compute_density` também retorna `None`
   quando o gate legitimo filtra tudo (`len(x) == 0`). Gate vazio vira 400 com
   mensagem de coluna inexistente — erro errado, o gate não está quebrado.

Efeito colateral relacionado: `apply_gate_filter` retorna o dataset **sem
filtrar** quando o canal do gate não existe na amostra (no-op silencioso). Uma
cópia de quadrante FITC × APC aplicada numa amostra sem esses canais conta 100%
do parent sem avisar ninguém — e a subárvore inteira herda a stat fictícia.

## Escopo

### 1. Reproduzir e diagnosticar

- Repetir o request do print: `GET /analytics/gate/<id>/density?x=FITC-A&y=APC-A`
  numa amostra sem esses canais e numa com dataset vazio pós-filtro; confirmar
  os dois 400 hoje indistinguíveis.
- Comparar `$PnN` entre os arquivos do ZIP do teste (o endpoint de metadados do
  BE-09 já expõe os canais por amostra) para confirmar a divergência de nomes.

### 2. Separar os casos no contrato

- Canal ausente → `400` com `detail` nomeando o(s) canal(is) pedido(s) que não
  existem naquela amostra (ex.: `"Canal 'FITC-A' não existe nesta amostra."`).
  O front precisa dessa informação para exibir causa real — ver FE-22.
- Dataset vazio pós-filtro → `200` com `total_events: 0` e payload de modo
  vazio (`histogram`/`x`/`y` vazios), não erro. Gate sem evento é estado válido.
- Manter `mode`, labels e shape da response inalterados nos demais casos.

### 3. Gate não aplicável: canal ausente invalida a linhagem (decisão 14/09 — [ADR-0016](../adr/0016-gate-nao-avaliavel-sem-canal.md))

Estilo FlowJo: a estratégia só vale até onde os parâmetros existem. Se o canal
de um gate não existe na amostra, **o gate e toda a subárvore abaixo dele são
não-aplicáveis ali** — não se registra stat.

- `apply_gate_filter` precisa distinguir "filtrou e sobrou 0 eventos" de "não
  pôde avaliar" (hoje os dois caminhos retornam um DataFrame — um vazio, um
  intacto). Sugestão: retornar `None` no caso não-avaliável ou levantar erro de
  domínio, e os chamadores tratam.
- `recalculate_gate_analysis` (`analytics/tasks.py`): ao bater num gate
  não-avaliável no caminho, gravar no `AnalysisResult.analysis_result` um
  marcador explícito — ex.: `{ "applicable": false, "reason":
  "missing_channels", "missing_channels": ["fitc_a"] }` — em vez de
  `summary_metrics` fictício, e propagar o mesmo marcador para os descendentes
  (a recursão já percorre `gate.children`). Marcador explícito, não ausência:
  ausente significa "ainda não calculado", outra coisa.
- `GateDensityView`/`GetGateDataView`: gate não-avaliável → `400` com `detail`
  nomeando o canal ausente (mesma família de erro do item 2, então o front tem
  um jeito só de exibir).
- Stats já gravadas com o no-op antigo ficam como estão — não migrar retroativo
  neste MR; recalcular sob demanda já reescreve com a semântica nova.
- `ApplyGateView`: cópia em amostra sem o canal **continua sendo criada** (o
  gate existe no painel, só não é avaliável ali) e nasce com o marcador — o
  relatório/árvore mostram o estado em vez de um número errado.

### 4. Aviso no momento da aplicação

O usuário não deve descobrir a divergência de painel por acidente. O apply (e o
`dry_run`) passa a devolver um bloco `non_evaluable` ao lado de `conflicts`:

```
{ "non_evaluable": [
    { "file_data_id": 11, "file_name": "ES488837.fcs",
      "missing_channels": ["FITC-A", "APC-A"],
      "affected_gate_ids": [51, 52] } ] }
```

- Montado comparando os canais que cada gate-fonte referencia (coords +
  dashboard_config) com as colunas da amostra de destino — barato, sem abrir o
  dataset inteiro se o header/`param_list` já estiver disponível.
- `dry_run` inclui o bloco para a UI avisar **antes** de confirmar; o apply real
  devolve o mesmo bloco para o toast/resumo pós-ação.
- Não bloqueia a aplicação — é aviso, não erro (a decisão do item 3 já define o
  destino da cópia).

## Arquivos a tocar

- `analytics/views.py` — `GateDensityView.get` (e o endpoint equivalente de
  arquivo, se ele compartilhar o mesmo caminho de erro); `ApplyGateView` ganha
  o bloco `non_evaluable` na response e no `dry_run`.
- `utils/density.py` — `compute_density`/`subsample_scatter`/`compute_histogram`
  precisam diferenciar "coluna ausente" de "0 eventos" (hoje ambos são `None`);
  `apply_gate_filter` para o sinal de canal ausente.
- `analytics/tests/` — casos: canal ausente, dataset vazio, gate cujo canal não
  existe na amostra.

## Critérios de aceite

- [ ] Density de gate/arquivo com canal inexistente → 400 com `detail` citando
  o canal e a amostra.
- [ ] Gate com 0 eventos pós-filtro → 200 com `total_events: 0`, sem erro.
- [ ] A resposta nunca mais diz "Columns not found" quando as colunas existem.
- [ ] Recalcular gate cujo canal falta na amostra → `analysis_result` com
  `applicable: false` + canais ausentes, sem `summary_metrics` fictício.
- [ ] Descendentes do gate não-avaliável recebem o mesmo marcador (linhagem
  cortada onde o parâmetro acaba).
- [ ] Gate avaliável continua com `summary_metrics` inalterado.
- [ ] Apply/`dry_run` para amostra sem o canal → response traz `non_evaluable`
  com arquivo, canais e gates afetados; a aplicação não é bloqueada.

## Fora de escopo

- Mapear/renomear canais entre aquisições ($PnS vs $PnN, painel por amostra) —
  se a investigação confirmar divergência de painel, vira PRD próprio.
- Mudar o que o front exibe — FE-22.
