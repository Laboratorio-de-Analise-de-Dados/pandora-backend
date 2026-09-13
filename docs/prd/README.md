# PRDs do backend

| PRD | Assunto | Status |
|---|---|---|
| [BE-01](BE-01-delete-file.md) | Desabilitar/reativar amostra | Entregue (#67) |
| [BE-02](BE-02-editar-experimento.md) | PATCH de experimento | Entregue (#68) |
| [BE-03](BE-03-excluir-gates-em-lote.md) | Exclusão de gates em lote | Entregue (#69) |
| [BE-04](BE-04-propagar-nome-cor-gate.md) | Propagar nome/cor por escopo | Entregue (#69) |
| [BE-05](BE-05-upload-fcs-solto.md) | `.fcs` solto e validação de extensão | Entregue (#70) |
| [BE-06](BE-06-fechar-pr-65.md) | Fechar a dívida do PR #65 | Resolvido (#66) |
| [BE-07](BE-07-subsamples.md) | Subsamples e caminho do ZIP | Parcial (#78); escopo de gates pendente |
| [BE-08](BE-08-historico-rollback.md) | Histórico e rollback da análise | Não iniciado |
| [BE-09](BE-09-headers-fcs.md) | Expor metadados do header FCS | Na branch `chore/ai-setup`, PR pendente |
| [BE-10](BE-10-identidade-de-conteudo-e-blob-compartilhado.md) | Identidade de conteúdo e blob compartilhado | Proposto |
| [BE-11](BE-11-copiar-mover-experimento.md) | Copiar/mover experimento entre contextos | Proposto |
| [BE-12](BE-12-dedup-no-upload.md) | Dedup no upload com aviso/reuso de blob | Proposto |

Fora dos PRDs, já entregues: desanexar cópia no reshape (#71), geometria com
escopo + sobrescrita ao aplicar (#72), autor do gate na árvore (#74), membros na
listagem de organizações (#75), gestão de roles/remoção de membros (#76),
`DELETE` de membership inativando (#77).

## Dívidas registradas

- Rotina de retenção/limpeza física (Parquet frio, ZIP órfão) e cota de storage —
  ver BE-01 e [ADR-0004](../adr/0004-zip-como-fonte-de-verdade-parquet-como-cache.md).
- Fluxo de reupload do mesmo arquivo (reativar × sobrescrever × criar nova), com
  identificação por hash — ver BE-01. Com o `source_path` do BE-07, "mesmo
  caminho = mesmo arquivo" já é a base. O `guid` do header FCS é um sinal
  complementar medido: único dentro de experimento na base atual (duplicatas
  só em re-upload entre experimentos) — ver BE-09.
- Migrar validações inline das views antigas para serializers —
  [ADR-0009](../adr/0009-validacao-em-serializers.md).
