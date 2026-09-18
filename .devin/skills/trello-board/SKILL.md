---
name: trello-board
description: Convenções do board "Pandora — Implementações" no Trello — colunas, etiquetas de prioridade/categoria e capas. Usar ao ler/mover cards ou verificar o que está pendente/entregue/em produção
allowed-tools:
  - mcp_call_tool
  - mcp_list_tools
  - exec
  - read
  - grep
  - glob
---

Board **"Pandora — Implementações"** (id `6aad992388d05be9a96dc1c4`, shortLink `dXb21KpL`) — gerenciado via MCP server `trello`. Sem board default: sempre passar `boardId`.

## Divisão Trello ↔ repos

- **Trello é a fonte de verdade de status**: pipeline (coluna), prioridade
  (etiqueta), progresso/aceitação (checklist do card) e eventos de
  entrega (comentário ou linha na descrição, ex.: "deploy v0.4.0").
- **Documentação durável**: PRDs vivem no repo privado `pandora-docs`
  (`pandora-docs/prd/`) = o quê; ADRs de domínio em `docs/adr/` e os
  sensíveis em `pandora-docs/adr/backend/` = o porquê. PRD/ADR **não** é
  editado para mudar status — se um campo de status do doc estiver
  desatualizado, o Trello é quem manda.
- **Todo card linka seus docs**: descrição do card aponta para
  `https://github.com/Laboratorio-de-Analise-de-Dados/pandora-docs/blob/main/prd/<arquivo>.md`.
  Card novo de feature = descrição com link do PRD + checklist de
  aceitação quando fizer sentido.
- `docs/TRACKER.md` segue existindo só para coordenar **área de arquivos
  entre sessões paralelas** — não duplicar status de pipeline nele.

## Modelo mental

- **Coluna = andamento** (onde o item está no pipeline — só isso)
- **Capa do card (cover) = categoria** do item (ver "Capas" abaixo)
- **Etiqueta = urgência** da task

## Colunas (esquerda → direita)

| Lista                              | id                         | Papel                                                        |
| ---------------------------------- | -------------------------- | ------------------------------------------------------------ |
| 🔍 Discovery                       | `6aadc2d3ab1fdb83e2607fa0` | Precisa de refinamento de produto/PRD antes de virar backlog |
| 📖 Docs — legendas                 | `6aada06e1eca37e675679dff` | Legendas + cards-base de capa (não são tasks)                |
| 📥 Backlog                         | `6aad993ca54e5ce241fb8dcc` | Tudo que está definido e não entrou no fluxo                 |
| 🔨 Em andamento                    | `6aad9acc21dde4bf061362eb` | Trabalho em execução (WIP 1–2/pessoa)                        |
| 📦 Entregue                        | `6aad994921dde4bf061127f5` | Mergeado na main, aguardando release                         |
| 🚀 Em produção                     | `6aad9ad79018b5ec2bafdf3d` | Deployado e verificado — fim                                 |

Regra de movimento: **Discovery → Backlog → Em andamento → Entregue → Em produção**.

- **Discovery**: item sai daqui quando tem definição suficiente (PRD no
  `pandora-docs` ou escopo claro na descrição) → move para 📥 Backlog.
- **"Fazer agora" não é mais coluna** — virou capa. Item urgente do ciclo
  fica no Backlog com a capa correspondente (+ etiqueta Urgente quando
  for o caso).
- Cards de "capa"/legenda no topo das colunas (📖 Docs) não são tasks —
  não mover nem tratar como trabalho.

## Capas (covers) = categoria

| Capa     | Categoria      | Significado                                    |
| -------- | -------------- | ---------------------------------------------- |
| azul/sky | roadmap        | Escopo planejado de produto                    |
| rosa     | dívida técnica | Refactor, limpeza, hardening                   |
| cinza    | arquivado      | Estacionado por decisão — retoma com gatilho   |
| laranja | fazer agora | Pendência do ciclo, executar já — substitui a antiga coluna |

**O MCP não seta `cover.color` em card existente.** Workaround provado:
`copy_card` preserva a capa — copie um card-base da coluna 📖 Docs e
edite nome/descrição:

- `6aada2db9b13723af0bc6ef4` — 📋 Card-base capa ⬛ (estacionado)
- `6aada33a4f4ab0dcbb7766b3` — 📋 Card-base capa 🩷 (dívida técnica)
- `6aada33f4b31c20a5cb9f9a2` — 📋 Card-base capa 🩵 (roadmap)
- `6aadc41e9edaa9a6db7e0b87` — 📋 Card-base capa 🟠 (fazer agora)

Para corrigir a capa de um card existente: copiar o card-base com o
conteúdo do card original, ajustar etiquetas e arquivar o antigo (o
link/URL muda — avisar o usuário).

## Etiquetas = urgência

| Label            | id                         | Cor       |
| ---------------- | -------------------------- | --------- |
| Urgente          | `6aad992388d05be9a96dc219` | 🔴 red    |
| Muito Importante | `6aad992388d05be9a96dc218` | 🟠 orange |
| Importante       | `6aad992388d05be9a96dc217` | 🟡 yellow |
| Baixa            | `6aad992388d05be9a96dc216` | 🟢 green  |
| Planejamento     | `6aad992388d05be9a96dc21a` | 🟣 purple |
| Ideias           | `6aad992388d05be9a96dc21b` | 🔵 blue   |

Etiquetas de **categoria** (`estacionado`, `dívida técnica`, `roadmap`)
são legado — a categoria agora é a **capa**. Ao tocar num card que ainda
as tem, remova a etiqueta de categoria e garanta a capa correspondente
(sem etiqueta de categoria nova). `update_card_details` com `labels`
**substitui** o array — sempre reenvie as etiquetas de urgência que o
card já tem.

## Sincronia Trello ↔ git/deploy

- Card só vai para 📦 Entregue quando mergeado na `main`.
- Card só vai para 🚀 Em produção depois de **release deployada** —
  conferir com `gh release list` + `gh run list --workflow release.yml`
  (skill `release` descreve o pipeline ADR-0022).
- Verificação feita em 2026-09: back `v0.4.0` + front `v0.7.0` deployados;
  as 23 features Q1–Q3 + bônus + BE-34/FE-34 (tags) estão em 🚀 Em produção.
