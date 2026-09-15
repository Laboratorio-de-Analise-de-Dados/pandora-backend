# BE-19 — Workspaces/templates: estratégia de análise reutilizável entre experimentos

**Repo:** pandora-backend (+ front) · **Item do doc:** direção levantada em 14/09 · **Tipo:** feature (exploratório) · **Base:** `main`
**Status:** não iniciado — PRD de visão; exige ADR próprio antes de implementar.
**ADRs relacionados:** [0003](../adr/0003-linhagem-de-gates-por-copied-from.md) (linhagem por `copied_from`), [0006](../adr/0006-identidade-de-amostra-e-subsample.md), [0008](../adr/0008-historico-append-only-de-analise.md), [0016](../adr/0016-gate-nao-avaliavel-sem-canal.md).

## Visão (registro da discussão de 14/09)

Workspace é a evolução do conceito de subsample: uma forma de **abstrair a
estratégia de análise** (árvore de gates + agrupamento) para ser reutilizada
em experimentos diferentes. O fluxo imaginado:

- o laboratório define uma estratégia nomeada (painel de canais esperado,
  subsamples, hierarquia de gates com geometrias);
- ao criar um experimento, o usuário parte dessa workspace — subsamples já
  existem antes do upload e as amostras se encaixam neles;
- gates que referenciam canais ausentes na aquisição aparecem
  **não-avaliáveis** (ADR-0016), sinalizando "configurado para outro canal"
  em vez de falhar silenciosamente;
- com o tempo, a aplicação da estratégia nas amostras pode ser automatizada.

## O que já existe como fundação

- Subsample com identidade por `source_path` e CRUD pela API (BE-07) —
  subsamples já podem ser criados antes do upload.
- Linhagem `copied_from` e propagação por escopo (ADR-0002/0003) — dentro do
  experimento.
- `ExperimentCopyView` clona a árvore inteira entre experimentos — o mecanismo
  de cópia cross-experimento já existe, mas copia o estado todo, não instancia
  um template.
- Histórico append-only (BE-08) — toda mutação da estratégia já é registrada.
- Gate não-avaliável por canal ausente (BE-18/ADR-0016) — a validação de
  painel que um template precisa ao ser instanciado.

## Questões abertas (para o ADR)

- **Identidade cross-experimento:** `copied_from` hoje não cruza
  experimento; derivar uma árvore de um template pede um vínculo novo
  (ex.: `template_gate_id`) ou um objeto `AnalysisTemplate`/`Workspace`
  próprio — não esticar `copied_from` além do experimento.
- **Deriva vs. vínculo vivo:** mudança na workspace propaga para os
  experimentos que a usam (linhagem viva, mais complexa) ou a workspace só
  serve de molde na criação (snapshot, simples)?
- **Match de amostras:** amostra entra no subsample pelo `source_path` do
  ZIP; com template, o encaixe seria por nome de diretório esperado, por
  metadado do FCS, ou manual?
- **Painel divergente:** o template declara os canais esperados? A
  marcação de não-avaliável (BE-18) já cobre a consequência, mas o ADR
  deve decidir se o painel é parte do contrato do template.

## Restrição de design — analisador não-humano

A construção deve permitir que um **agente de análise não-humano** entre no
futuro (serviço externo de ML — projeto "juvia", repo próprio ainda não
iniciado). O que isso já exige hoje:

- mutações de análise sempre via API, nunca por atalho interno — o log
  (BE-08) já trata qualquer ator igual;
- identidade do autor na revisão deve comportar um ator de serviço, não só
  usuário — hoje `created_by` é FK de User; quando o serviço existir, a
  decisão é usuário-que-disparou vs. identidade própria (ADR de fronteira);
- uma rodada de ML aparece naturalmente como uma **sessão** na timeline do
  BE-20 — o checkpoint "antes da análise automática" é o ponto de retorno.

Nada a construir agora: é critério para as decisões de workspace/checkpoint
não fecharem a porta. O PRD/ADR da fronteira nasce quando o juvia existir.

## Fora de escopo

- Automação completa da aplicação (auto-gating) — depende da definição da
  workspace existir primeiro.
- UI — nasce um PRD de front quando o modelo estiver decidido.
