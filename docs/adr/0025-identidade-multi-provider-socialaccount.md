# ADR-0025 — Identidade de usuário como conta central vinculável a N providers

- **Status:** Aceito
- **Data:** 2026-09-17
- **Contexto do código:** `accounts/models.py`, `accounts/views.py` (callbacks OAuth), `accounts/services/oauth.py`, `accounts/urls.py`

## Contexto

O login social (Google/Microsoft) identifica o usuário **somente por email**:
`get_or_create(email__iexact=...)` + `auth_provider` (CharField único que
guarda o último provider usado). Isso funciona enquanto cada pessoa usa um
único email, mas a realidade já mostrou o contrário: a mesma pessoa tem
conta pessoal (`hotmail`) e institucional (`aluno.fiocruz.br`) — hoje viram
dois usuários isolados, sem como convergir.

Também não há trilha de auditoria de autenticação: ninguém responde "quem
vinculou o quê, quando" — relevante para LGPD e para o suporte
("desconectei a conta errada").

## Decisão

A conta (`User`) é a entidade central; providers externos são vínculos
explícitos nela.

1. **`SocialAccount`** (`provider`, `provider_user_id`/`sub`, `email`,
   `user`, `active`) registra cada identidade vinculada. Nunca é apagada —
   desvincular marca `active=false` (mesmo invariante de soft delete do
   resto do sistema).
2. **`AuthEvent`** (`user`, `action`, `provider`, `metadata`, `summary`,
   `created_at`) é o log append-only de eventos de autenticação: login via
   SSO, vínculo, desvínculo, merge. Mesmo espírito do `AnalysisRevision`
   (append-only, summary pronto pra UI), modelo separado porque eventos de
   conta não pertencem ao domínio de experimentos.
3. **Login social casa por `sub`** (identificador imutável do provider)
   quando o vínculo existe; email-match continua como fallback de
   primeiro contato.
4. **Email-match no login exige confirmação**: se o email do IdP coincide
   com uma conta existente ainda sem vínculo para aquele provider, o fluxo
   não loga direto — avisa "continuar vincula esta identidade à conta X";
   confirmar vincula, cancelar devolve ao login local.
5. **Desvínculo é livre e auditado**, mas remover o último método de acesso
   exige definir o email principal e senha da conta (senão o usuário se
   tranca fora).

## Alternativas consideradas

### A) Manter match só por email

Descartada: email é mutável e atribuído pelo provider; `sub`/`oid` é a
identidade imutável. Match por email como única âncora também abre
janela de takeover se um provider entregar email não verificado.

### B) Auto-join de organização por domínio de email no login SSO

Descartada e removida nesta entrega — domínio não é boundary de confiança
(`@hotmail.com` agruparia estranhos). Ver ADR-0026.

### C) django-allauth / authlib como framework completo

Descartada: o fluxo OAuth já está implementado e funcionando; migrar para
um framework inteiro para ganhar a tabela de social accounts trocaria um
problema pequeno por uma reescrita.

## Consequências

- Vínculo de identidade é explícito e sobrevive a troca de email no IdP.
- `auth_provider` no `User` passa a ser informativo (último/criador) —
  a fonte de verdade dos providers é `SocialAccount`.
- O merge de contas (duas `User` → uma) fica para PRD própria (BE-30) —
  é a dívida consciente: enquanto não existir, duas contas da mesma
  pessoa continuam coexistindo.
- `AuthEvent` cresce a cada login SSO — volume trivial, mas se um dia
  incomodar, arquivamento/partição é decisão nova.
