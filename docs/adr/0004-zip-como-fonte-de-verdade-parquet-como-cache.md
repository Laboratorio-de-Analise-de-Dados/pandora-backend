# ADR-0004 — ZIP/FCS é fonte de verdade; Parquet é cache regenerável

- **Status:** Aceito
- **Data:** 2026-08-27
- **Contexto do código:** `fcs_parser/models.py` (`FileDataModel.load_data_set`, `parquet_path`), `fcs_parser/services/process_experiment_file.py`

## Contexto

O ingest converte cada FCS em Parquet para ler colunas rápido nos plots. Passamos
a ter dois artefatos do mesmo dado e, a cada operação de limpeza ou migração,
a pergunta "qual dos dois eu posso perder?" reaparece.

## Decisão

O arquivo enviado (ZIP ou `.fcs` solto) é a fonte de verdade e **não é apagado**.
O Parquet é cache: se estiver ausente ou corrompido, `load_data_set` reextrai o
FCS do ZIP pelo `source_path` e regrava o Parquet. Nenhuma feature pode depender
de dado que exista **apenas** no Parquet.

## Alternativas consideradas

### A) Parquet como fonte de verdade e descarte do ZIP

Descartada: perde metadados do FCS que ainda não são lidos hoje e impede
reprocessar com uma versão melhor do parser. Reanálise de citometria olhando
metadado antigo é caso real.

### B) Guardar os dois em banco (bytea/BLOB)

Descartada: volume de citometria em coluna de banco inviabiliza backup e
restore; o Postgres viraria storage de arquivo.

## Consequências

- Limpeza de disco pode mirar Parquet frio sem perda semântica — é o único
  cleanup seguro do v0.
- ZIP órfão (sem experimento associado) vira o candidato natural para a rotina de
  retenção futura; enquanto não existir, o volume só cresce (ADR-0001).
- Regeneração é lenta: a primeira leitura após perder o Parquet paga a extração.
