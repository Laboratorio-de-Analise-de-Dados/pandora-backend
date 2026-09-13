# BE-05 — Aceitar `.fcs` solto e mensagem clara de extensão inválida

**Repo:** pandora-backend · **Item do doc:** 2 (parte backend) · **Tipo:** bug/feature · **Base:** `main`
**Branch sugerida:** `fix/upload-extension-validation`
**Status:** Entregue no PR #70.

## Problema

O doc pede para informar o usuário que o upload deve ser `.fcs` e/ou `.zip`. Hoje o backend só valida ZIP:

```python
# utils/validators.py
def validate_zip_file(file):
    ext = os.path.splitext(file.name)[1]
    if ext.lower() != '.zip':
        raise ValidationError('O arquivo deve ser um arquivo ZIP.')
```

Consequências: (a) um `.fcs` solto é rejeitado, contrariando o comportamento esperado; (b) o upload chunked (`ExperimentInitView` / `UploadChunkView`) não valida extensão nenhuma no início — só quebra no `complete/`, depois do usuário subir o arquivo inteiro.

## Escopo

1. Validador único de extensão aceitando `.zip` e `.fcs`, com mensagem em pt-BR listando as extensões aceitas.
2. Validar a extensão **no início** do fluxo chunked, para falhar antes do upload.

### Contrato

```
POST /experiment/init/
Body: { "title", "type", "totalChunks", "fileName", "organizationId"? }

400 { "detail": "Extensão não suportada. Envie um arquivo .fcs ou .zip." }
```

`fileName` passa a ser aceito (e validado) no init. Manter compatibilidade: se `fileName` não vier, não quebrar — apenas seguir validando no `complete/`.

### Regras

- `utils/validators.py`: `validate_experiment_file_extension(file_or_name)` aceitando `{".zip", ".fcs"}`, case-insensitive; manter `validate_zip_file` como wrapper se algo mais usar.
- `ExperimentSerializer.validate` passa a usar o validador novo.
- `ExperimentCompleteView`: se o arquivo final for `.fcs`, tratar como amostra única (o `RecomputeFileDataView` já tem caminho legado para `fcs_path`) em vez de tentar extrair um ZIP. Verificar o fluxo de `complete/` antes de codar e, se o suporte a `.fcs` solto for grande, **quebrar este MR em dois**: (a) mensagens/validação de extensão, (b) suporte ao `.fcs` solto.
- Mensagens de erro sempre em pt-BR com a chave `detail`, como no resto das views.

## Arquivos a tocar

- `utils/validators.py`
- `fcs_parser/serializers.py` (`ExperimentSerializer.validate`)
- `fcs_parser/views.py` (`ExperimentInitView.post`, `ExperimentCompleteView`)
- `fcs_parser/tests/`

## Critérios de aceite

- [ ] `init/` com `fileName` de extensão inválida (ex.: `.csv`) → 400 com mensagem citando `.fcs` e `.zip`, e nenhum experimento criado.
- [ ] `init/` sem `fileName` → segue funcionando (retrocompatível).
- [ ] Upload de `.zip` continua funcionando ponta a ponta.
- [ ] Upload de `.fcs` solto gera um experimento com 1 `FileDataModel` processável (ou, se o MR foi quebrado, existe issue registrada para a parte b).
