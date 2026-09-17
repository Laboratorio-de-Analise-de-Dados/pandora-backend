# PRDs do backend

| PRD | Assunto | Status |
|---|---|---|
| [BE-01](BE-01-delete-file.md) | Desabilitar/reativar amostra | Entregue (#67) |
| [BE-02](BE-02-editar-experimento.md) | PATCH de experimento | Entregue (#68) |
| [BE-03](BE-03-excluir-gates-em-lote.md) | Exclusão de gates em lote | Entregue (#69) |
| [BE-04](BE-04-propagar-nome-cor-gate.md) | Propagar nome/cor por escopo | Entregue (#69) |
| [BE-05](BE-05-upload-fcs-solto.md) | `.fcs` solto e validação de extensão | Entregue (#70) |
| [BE-06](BE-06-fechar-pr-65.md) | Fechar a dívida do PR #65 | Resolvido (#66) |
| [BE-07](BE-07-subsamples.md) | Subsamples e caminho do ZIP | Entregue (#78, partes 1 e 2) |
| [BE-08](BE-08-historico-rollback.md) | Histórico e rollback da análise | Backend em `fix/gate-density-missing-channel`; front é MR à parte |
| [BE-09](BE-09-headers-fcs.md) | Expor metadados do header FCS | Entregue (#80) |
| [BE-10](BE-10-identidade-de-conteudo-e-blob-compartilhado.md) | Identidade de conteúdo e blob compartilhado | Implementado em `feat/file-identity-copy` |
| [BE-11](BE-11-copiar-mover-experimento.md) | Copiar/mover experimento entre contextos | Implementado em `feat/file-identity-copy` |
| [BE-12](BE-12-dedup-no-upload.md) | Dedup no upload com aviso/reuso de blob | Implementado em `feat/file-identity-copy` |
| [BE-13](BE-13-escopo-permissao-e-soft-delete.md) | Escopo de permissão em todo lookup + soft delete de experimento | Implementado em `feat/scoped-lookups` |
| [BE-14](BE-14-reativar-experimento.md) | Reativar experimento (caminho de volta do soft-delete) | Implementado em `feat/scoped-lookups` |
| [BE-15](BE-15-rename-citosharp-pandora.md) | Renomear pacote `citosharp` → `pandora` | Implementado em `feat/scoped-lookups` |
| [BE-16](BE-16-titulo-unico-apenas-ativos.md) | Título único apenas entre ativos | Implementado em `feat/scoped-lookups` |
| [BE-17](BE-17-creator-name-na-listagem.md) | `created_by_name` na listagem de experimentos | Implementado em `feat/created-by-name-list`                   |
| [BE-18](BE-18-density-erro-canais.md) | "Erro ao carregar dados" em gate/arquivo sem o canal pedido | Implementado em `fix/gate-density-missing-channel` |
| [BE-19](BE-19-workspaces-templates-analise.md) | Workspaces/templates: estratégia reutilizável entre experimentos | Visão (exige ADR próprio) |
| [BE-20](BE-20-checkpoints-de-analise.md) | Checkpoints: salvar ponto nomeado e restaurar o experimento até ele | Implementado em `feat/analysis-checkpoints` (ADR-0017 Aceito) |
| [BE-21](BE-21-metadados-visuais-listagem.md) | `my_role`, `progress` e preview/thumbnail na listagem de experimentos (dep. do FE-26) | Implementado em `feat/analysis-checkpoints` |
| [BE-22](BE-22-compensacao.md) | Compensação: aceitar `$SPILLOVER` do FCS ou calcular de controles (subsamples) | Implementado em `feat/analysis-checkpoints` (ADR-0018/0019) |
| [BE-23](BE-23-analise-colaborativa-branch-merge.md) | Análise colaborativa: branches de análise, merge e conflitos (visão git-like) | Visão — precisa de ADR antes |
| [BE-24](BE-24-metadados-e-criacao-sem-arquivo.md) | Criar experimento sem arquivo (`POST /experiment/`), `description` opcional e `values` read-only | Implementado em `feat/analysis-checkpoints` |
| [BE-28](BE-28-tipo-de-experimento-vocabulario.md) | Tipo de experimento como vocabulário controlado  (admin ou dono do experimento; `/experiment/types/` + dedup normalizado) | Implementado em `feat/experiment-type` (ADR-0023) |

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
  [ADR-0009](../adr/0009-validacao-em-serializers.md). Resolvido no fluxo de
  upload (BE-13); segue aberto para `FileSubsampleView` e demais pontos com
  checagem manual de campo.
