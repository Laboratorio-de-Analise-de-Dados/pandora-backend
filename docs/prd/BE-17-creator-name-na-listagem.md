# BE-17 — `created_by_name` na listagem de experimentos

**Repo:** pandora-backend · **Tipo:** feature (micro) · **Base:** `main`
**Status:** não implementado — documentado para o agente do back.
Front que consome: `feat/card-owner-org` (rodapé do card de experimento).

## Problema

O card de experimento no front vai exibir o criador e a organização
(rodapé: username à esquerda, org à direita). A org já chega aninhada via
`OrganizationListSerializer`, mas `created_by` serializa só o id — não há
como mostrar o nome do criador sem o campo.

## Implementação

Uma linha no `ListExperimentSerializer` (`fcs_parser/serializers.py`):

```python
class ListExperimentSerializer(serializers.ModelSerializer):
    values = serializers.ListField(child=serializers.CharField())
    organization = OrganizationListSerializer(read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.username", read_only=True
    )
    ...
```

`User` é `AbstractUser` — `username` existe. O front já trata o campo como
opcional (`created_by_name?: string | null`), então não há janela de
quebra se o deploy do back sair depois do front.

## Critérios de aceite

- [ ] `GET /experiment/` devolve `created_by_name` com o username do
      criador em cada item.
- [ ] Campo somente leitura — não entra em payload de PATCH/POST.
