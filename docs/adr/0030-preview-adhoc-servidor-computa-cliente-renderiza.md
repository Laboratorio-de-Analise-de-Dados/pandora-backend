# ADR-0030 — Preview ad-hoc: o servidor computa, o cliente só renderiza

- **Status:** Aceito
- **Data:** 2026-09-29
- **Contexto do código:** `fcs_parser/views.py`
  (`CompensationPreviewView`), `utils/density.py`, FileBasedCache

## Contexto

A edição manual de compensação precisa de feedback visual ao vivo —
cada tecla na matriz muda o plot. A alternativa seria mandar os eventos
brutos pro browser e compensar client-side, mas isso exige máquina forte
do usuário (matriz N×C × centenas de milhares de eventos em JS, payload
pesado por troca de amostra). O requisito de produto: software leve no
cliente — o servidor pode sofrer mais, desde que sem desconforto.

## Decisão

**Divisão de responsabilidade fixa: o servidor trabalha, o cliente só
desenha.** O browser nunca recebe eventos brutos — recebe agregados
(grid de densidade, `channel_stats`). O backend aplica a matriz-rascunho
nos eventos, bina no grid e devolve ~40k floats + estatísticas na mesma
resposta.

Três regras derivadas:

1. **Cache por conteúdo, não por id.** O cache de densidade chaveava por
   `compensation_id`; no preview a matriz muda a cada tecla, então a
   chave inclui `sha256` do payload normalizado (`channels` + `matrix` +
   `file` + eixos + render params + `gate`/`gates`). Voltar a um valor já
   digitado custa zero; não colide com cache de matrizes persistidas; é
   mais uma chave no mesmo FileBasedCache (ADR-0004), sem camada nova.
2. **Preview não persiste nada.** Não cria `CompensationMatrix` (nem
   inativa) e não grava `AnalysisRevision` — não altera resultado nem
   polui a timeline. Persistência é só via ação explícita do usuário.
3. **Um request por edição.** `channel_stats` (mediana/mean/count por
   canal × população) viaja **na mesma resposta** da densidade — nunca
   densidade + N requests de stats. Debounce ~400 ms no front.

## Alternativas consideradas

### A) Compensar no browser (eventos crus + matemática em JS)

Descartada — contrário direto do requisito "cliente leve": payload de
MBs por amostra, CPU do cliente a cada tecla, e um segundo renderer de
densidade pra manter. Só se repensa se surgir requisito de prévia
instantânea por tecla (sem debounce) — hoje o debounce é confortável.

### B) Persistir cada versão da matriz em edição

Descartada — poluiria o modelo e a timeline com rascunhos; o usuário
decide quando criar (`Criar`/`Criar e aplicar`).

### C) Cache dedicado / TTL especial pro preview

Descartada — herda o cache do density (mesma infra, mesma retenção);
chave por hash já resolve o problema de invalidação sem camada nova.

## Consequências

- **Mais fácil:** máquina de laboratório fraca usa a feature; custo por
  edição cai pra zero em valores repetidos; um request por edição mantém
  a UI simples.
- **Mais difícil / a monitorar:** cada request relê o parquet (custo
  dominante) — se amostras >1M eventos ficarem lentas, otimizar
  server-side nesta ordem: cache do dataframe em memória → subsampling
  server-side (densidade satura ~200k eventos num grid 200×200) → só
  então repensar client-side. Ambos invisíveis pro usuário.
- **Dívida assumida:** desenhar gate dentro do preview fica pra fase 2;
  `distribution` de figuras (BE-33) usa a mesma divisão — curvas vêm do
  density live.
