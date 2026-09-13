# ADR-0010 — Convite sem link por e-mail e aceite autenticado

- **Status:** Aceito
- **Data:** 2026-08-20 (registrado em 2026-09-13)
- **Contexto do código:** `accounts/views.py` (`InviteAcceptView`), `accounts/serializers.py`

## Contexto

Convite para uma organização precisa levar a pessoa a um vínculo
(`Membership`) com uma `role`. Mandar um link com token no e-mail é o padrão da
indústria, mas transforma o token em credencial: quem tiver acesso ao e-mail
(ou ao log de um gateway) entra na organização sem autenticar.

Além disso, o cadastro público não pode aceitar `organization_id`/`role`, senão
qualquer um se autoinsere em um laboratório.

## Decisão

O e-mail é apenas informativo: **não contém link com token**. O convidado faz
login/cadastro com o mesmo e-mail, vê o convite pendente no sininho do header e
aceita ali. O `POST /invites/accept/<token>/` exige `IsAuthenticated` e confere
que o e-mail do usuário logado é o do convite; o token existe, mas é usado
internamente pelo front. Aceitar cria `Membership` ativo na organização que
convidou, com a `role` do convite.

## Alternativas consideradas

### A) Link com token no e-mail (aceite sem login)

Descartada: token no e-mail vira credencial de acesso e não há como provar quem
aceitou — inaceitável em um sistema cuja proposta é rastreabilidade de quem
editou a análise.

### B) Aceite sem checar o e-mail do usuário logado

Descartada: qualquer pessoa autenticada com o token entraria no grupo.

## Consequências

- Um passo extra para o convidado (precisa estar logado), em troca de saber
  exatamente qual conta aceitou.
- Convite para e-mail que ainda não tem conta depende do cadastro; o convite
  fica pendente até lá.
- A listagem de organizações precisa expor os membros, senão o convite aceito
  "não aparece no grupo" — foi exatamente o bug corrigido no PR #75.
