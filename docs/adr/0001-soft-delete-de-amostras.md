# ADR-0001 — Desabilitar amostra em vez de apagar

- **Status:** Aceito
- **Data:** 2026-08-27
- **Contexto do código:** `fcs_parser/models.py` (`FileDataModel.active`), `DisableFileDataView`, `EnableFileDataView`

## Contexto

O relatório de testes pedia "deletar arquivo do experimento". Apagar o
`FileDataModel` levaria com ele os gates da amostra (cascade) e o Parquet, e o
dado bruto (FCS) só existe dentro do ZIP enviado — se o usuário apagar por
engano, a única recuperação é pedir o arquivo de volta ao dono do experimento,
sem nenhuma garantia de que ele reenviará exatamente o mesmo arquivo que foi
analisado.

## Decisão

A amostra é **desabilitada** (`active=False` + `deactivated_at`/`deactivated_by`),
não apagada. Amostra desabilitada sai das listagens e das análises (HTTP 409 em
endpoints de dados), mas Parquet, ZIP e gates continuam intactos e o
`/enable` a traz de volta ao estado anterior. O front ganha um filtro
"mostrar desabilitados".

## Alternativas consideradas

### A) Hard delete do registro e dos arquivos

Descartada: perde os gates (que representam o trabalho de análise, não o dado)
e é irreversível justamente no cenário mais provável de uso — clicar errado na
árvore de amostras.

### B) Hard delete do registro, mantendo os arquivos em disco

Descartada: produz arquivo órfão em disco sem nenhuma referência no banco, ou
seja, o pior dos dois mundos (o dado ocupa espaço e ninguém consegue usá-lo).

## Consequências

- Reversível e barato: desabilitar é um `UPDATE`.
- **O disco só cresce.** Não existe rotina de retenção; um usuário que sobe ZIPs
  indefinidamente esgota o volume. Mitigação futura em `prd/BE-01-delete-file.md`
  (limpeza de Parquet frio, que é regenerável — ver ADR-0004) e cota por
  usuário/organização. Enquanto isso, é uma fragilidade conhecida do sistema.
- Todo endpoint que lê dados precisa lembrar de filtrar `active=True`; o
  esquecimento aparece como "amostra desabilitada volta a aparecer no relatório".
