# ADR-0026 — Organização não deriva de domínio de email nem de tenant de IdP

- **Status:** Aceito
- **Data:** 2026-09-17
- **Contexto do código:** `accounts/views.py` (callbacks OAuth — bloco
  removido), `accounts/models.py` (Organization/Membership/Invite)

## Contexto

Os callbacks OAuth criavam uma `Organization` com o nome do domínio do
email e inseriam o usuário como MEMBER — "link automático por domínio".
Comportamento observado: login com `pmoro@aluno.fiocruz.br` criou a org
`aluno.fiocruz.br`; uma conta com `mail` junk da Microsoft criou
`fiocruzbr.onmicrosoft.com`.

Isso tem dois problemas de confiança:

1. **Domínio de email não é boundary de autorização.** Qualquer pessoa da
   instituição inteira (não só o lab) entraria na mesma org; pior, contas
   pessoais (`@hotmail.com`, `@gmail.com`) criariam orgs compartilhadas
   entre completos estranhos — vazamento de visibilidade de experimentos.
2. **Tenant de IdP tem granularidade errada.** Uma instituição (Fiocruz)
   contém vários laboratórios; a org do Pandora é lab/grupo de trabalho,
   não instituição. Mapear tenant→org agruparia o que deveria ser separado.

## Decisão

Login social cria **somente o usuário** — nenhuma organização é criada nem
associada. A única via de entrada em org continua sendo o fluxo de convite
(já existente: admin convida por email, convidado aceita autenticado) e a
criação manual de org pelo próprio usuário.

## Alternativas consideradas

### A) Manter auto-join apenas quando a org já existe

Descartada para o beta: "entra quem tem o domínio" ainda é boundary de
email, não de confiança. Pode voltar como configuração **opt-in por org**
("domínios permitidos") se um dia a demanda aparecer — decisão nova.

### B) Mapear tenant do IdP (`tid` claim) para org

Descartada: granularidade errada (instituição ≠ lab), contas pessoais não
têm tenant organizacional, e acopla o modelo de grupos do app ao modelo
de diretório de terceiros.

## Consequências

- Org volta a ser boundary de confiança real: só entra quem foi convidado.
- Usuário SSO novo nasce sem org — "Meus experimentos pessoais" é o
  ponto de partida, que já existia e funciona.
- Fica mais difícil: onboarding institucional depende de convite manual
  (nenhum "entre no seu lab automaticamente").
- Dívida registrada: auto-join opt-in por org (lista de domínios
  permitidos configurada pelo admin da org) é o refinamento futuro caso
  o onboarding manual doa.
