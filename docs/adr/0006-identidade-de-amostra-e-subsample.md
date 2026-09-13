# ADR-0006 — Identidade da amostra é o caminho no ZIP; subsample é tabela

- **Status:** Aceito — identidade de conteúdo parcialmente substituída por ADR-0012 (`source_path` vira dica de agrupamento)
- **Data:** 2026-09-12
- **Contexto do código:** `fcs_parser/models.py` (`FileDataModel.source_path`, `SubsampleModel`), `fcs_parser/services/process_experiment_file.py`, migrations `0010`/`0011`

## Contexto

A extração do ZIP guardava só o basename (`a1.fcs`) e ignorava as subpastas. Em
citometria a pasta **é** informação: `tempo_1/a1.fcs` e `tempo_2/a1.fcs` são a
mesma amostra em dois pontos do experimento, e com basename as duas colidiam
dentro do mesmo experimento — inclusive na hora de reextrair do ZIP, onde o
match por sufixo podia devolver o arquivo errado.

Além disso, "aplicar em todas as amostras" tratava o experimento inteiro como
escopo único, quando muitas vezes os controles se referem a um subconjunto.

## Decisão

Duas coisas separadas:

1. **Identidade:** `FileDataModel.source_path` guarda o caminho relativo dentro
   do ZIP (`tempo_1/a1.fcs`), com unicidade por `(experiment, source_path)` para
   caminhos não vazios. A reextração casa caminho exato e só cai no sufixo para
   dados legados. O nome exibido continua o basename.
2. **Agrupamento:** `SubsampleModel` (experimento, nome, `source_path`, autor,
   `active`) é uma entidade de primeira classe, e `FileDataModel.subsample` é uma
   FK **nullable**. A engine cria um subsample por diretório encontrado no ZIP;
   **arquivo na raiz do ZIP fica sem subsample** e o usuário agrupa na UI se
   quiser. A engine sugere, o cliente decide: renomear o subsample, criar um
   manualmente e mover amostras são operações da UI, e a extração não reescreve
   a escolha do cliente.

Backfill em duas etapas, porque o ZIP ainda está no disco e é a fonte de
verdade (ADR-0004):

1. Migration `0011`, barata e automática: `source_path = file_name` quando o
   nome é único no experimento; homônimos ficam com `source_path=""` (a
   constraint ignora vazios).
2. `manage.py repair_source_path`, laborioso e manual: abre o ZIP e redescobre
   o caminho real. Nome que aparece uma vez no ZIP é remapeado **preservando a
   linha** (gates e Parquet intactos). Nome repetido é ambíguo — não há como
   saber qual linha é qual pasta —, e só com `--recreate` as linhas ambíguas
   são **inativadas** e uma amostra nova é criada por entrada do ZIP. Os gates
   das antigas ficam presos às amostras inativas: perda aceita no v0, por isso
   o default é `--dry-run`-friendly e a recriação é opt-in.

## Alternativas consideradas

### A) Só `source_path`, com o subsample derivado do prefixo do caminho

Descartada: o agrupamento passaria a ser uma string derivada, então renomear um
subsample exigiria reescrever o caminho de todas as amostras — ou seja, mexer na
identidade para mudar um rótulo. E mover uma amostra para outro grupo seria uma
mentira sobre onde ela estava no ZIP.

### B) Campo texto `subsample` no `FileDataModel`

Descartada: sem entidade não há como listar subsamples vazios, renomear em um
lugar, guardar autor, nem usar o subsample como escopo de propagação sem
comparar strings — o mesmo erro do agrupamento por nome de gate (ADR-0003).

### C) Deduzir grupo por metadado do FCS (`$WELLID`, tubo etc.)

Descartada para o v0: depende do rigor do operador do citômetro e varia por
equipamento. Fica como enriquecimento futuro **em cima** da tabela, não em lugar
dela.

### D) Unicidade por `(experiment, file_name)`

Descartada: é exatamente o bug — proíbe o caso legítimo (mesmo nome em pastas
diferentes) e permite o ilegítimo (dois arquivos indistinguíveis no mesmo
diretório).

## Consequências

- Reextração determinística e mesmo basename permitido em pastas diferentes.
- Subsample passa a ser o escopo natural de "aplicar em todas" — implementação
  pendente, ver `prd/BE-07-subsamples.md`.
- Homônimos legados só ganham identidade forte depois de rodar
  `repair_source_path`; com `--recreate`, ao custo das análises feitas neles.
- Uma amostra pertence a **um** subsample (FK). Se um dia uma amostra precisar
  estar em dois grupos (ex.: "tempo_1" e "controles"), isso vira M:N e este ADR
  é substituído.
