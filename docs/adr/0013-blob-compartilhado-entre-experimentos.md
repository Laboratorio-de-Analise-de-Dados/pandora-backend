# ADR-0013 — Blob físico compartilhado entre experimentos

- **Status:** Proposto
- **Data:** 2026-09-13
- **Contexto do código:** `fcs_parser/models.py` (`FileModel.experiment`
  OneToOne, `FileDataModel.file`), rotinas de limpeza/retenção

## Contexto

`FileModel` (o arquivo upado — ZIP ou `.fcs` solto) é `OneToOne` com
`ExperimentModel`: cada experimento "dono" do seu blob. Isso torna cópia de
experimento e dedup de upload impossíveis sem duplicar bytes — e qualquer
limpeza assume que o arquivo morre com o experimento.

## Decisão

`FileModel` perde a posse do experimento e vira "arquivo guardado": path,
`sha256`, tamanho. O vínculo experimento↔blob passa a existir só via
`FileDataModel.file` (que já existe). Consequências de ciclo de vida:

- N experimentos podem referenciar o mesmo `FileModel`.
- Deleção física só de `FileModel` sem `FileDataModel` apontando (órfão) —
  o cascade de experimento deixa de ser gatilho de limpeza de bytes.
- A rotina de retenção (política freeze/delete do BE-10) e o GC de Parquet
  passam a olhar referências, não posse.

## Alternativas consideradas

### A) `FileModel` extra apontando pro mesmo path (sem mudar o schema)

Funciona no curto prazo (`file.name` é string de path), mas mantém dois
"donos" declarados do mesmo arquivo — a limpeza de um quebra o outro. É a
armadilha que a decisão evita.

### B) Tabela `StoredFile` separada desde já

Descartada por ora. `FileModel` já é quase isso — renomear conceito em vez
de criar tabela duplica trabalho de migração sem ganho. Se surgir necessidade
(metadados de blob próprios, versionamento), a extração fica natural.

### C) Manter 1:1 e duplicar bytes na cópia

Descartada — derrota o objetivo (storage livre, upload instantâneo na cópia).

## Consequências

- Copiar/mover experimento (BE-11) e reuso de upload (BE-12) viram operações
  de linha, não de bytes.
- A limpeza física passa a exigir contagem de referências — nunca apagar
  blob ainda referenciado (a política freeze/delete do BE-10 depende disso).
- **Dívida:** código existente que assume `FileModel.experiment` 1:1 precisa
  ser auditado na implementação (serializers, upload flow, limpezas).
