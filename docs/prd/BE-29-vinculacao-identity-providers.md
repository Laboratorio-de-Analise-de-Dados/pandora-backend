# BE-29 — Vinculação de identity providers à conta (SocialAccount + AuthEvent)

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch sugerida:** `feat/account-linking`
**Status:** em andamento.
**ADR:** `docs/adr/0025` (modelo), `docs/adr/0026` (sem org por domínio)
**Front:** `pandora-front/docs/prd/FE-31`

## Problema

A mesma pessoa com dois emails (ex.: `paulohenrikk@hotmail.com` pessoal +
`pmoro@aluno.fiocruz.br` institucional) vira dois usuários isolados. Não há
como vincular identidades, desvincular, nem auditar eventos de auth.
Adicionalmente, login social casa só por email — `sub`/`oid`, a identidade
imutável do provider, não é registrada.

## Escopo

### 1. Modelo `SocialAccount`

```
provider          CharField  ("google" | "microsoft")
provider_user_id  CharField  (sub do id_token / oid)
email             EmailField (email resolvido do provider, informativo)
user              FK(User, related_name="social_accounts")
active            Boolean    (default True)
linked_at         DateTime   (auto_now_add)
unlinked_at       DateTime   (null)
```

- UniqueConstraint: `(provider, provider_user_id)` — uma identidade de IdP
  pertence a um único usuário.
- Index `(user, active)` para listagem do perfil.
- Nunca deleta: desvincular → `active=False` + `unlinked_at`.

### 2. Modelo `AuthEvent` (log append-only)

```
user        FK(User, null=True)
action      CharField  ("login_sso" | "link" | "unlink" | "merge" | "login_local")
provider    CharField  ("" para local)
target_user FK(User, null=True)  # reservado para o merge (BE-30)
metadata    JSONField  (email do IdP, oid, ip, user-agent)
summary     CharField  (texto pronto para UI)
created_at  DateTime   (auto_now_add)
```

Registra login SSO, vínculo, desvínculo e (futuro) merge.

### 3. Login social — match por `sub`, depois email

- `SocialAccount` ativo com `(provider, sub)` → loga o `user` dono.
- Sem vínculo e email coincide com `User` existente → **modo aviso**: a
  resposta não emite JWT; o callback redireciona ao front com
  `?link_notice=1&email=...&provider=...` + token de confirmação
  (signed, curto) — o front exibe "continuar vincula esta identidade à
  conta <email>" → `POST /accounts/auth/<provider>/confirm-link/` com o
  token → backend cria o `SocialAccount` e emite JWT. Cancelar volta ao
  login.
- Sem vínculo e email novo → cria usuário + `SocialAccount` (fluxo atual).

### 4. Endpoints de vínculo (perfil)

```
GET  /accounts/users/me/social-accounts/      lista vínculos ativos
POST /accounts/auth/<provider>/link/          inicia OAuth em modo link (autenticado)
POST /accounts/auth/<provider>/link/callback/ conclui vínculo (state=link)
POST /accounts/social-accounts/<id>/unlink/   desvincula
```

- Modo link: `state` carrega contexto `link` + user id; o callback vincula
  em vez de logar.
- Se a identidade já pertence a **outro** usuário → 409, aponta para o
  merge (BE-30, fora de escopo aqui).

### 5. Unlink — regra do último acesso

- Sempre permitido e auditado (`active=False` + AuthEvent).
- Se for o último método de acesso (sem senha utilizável e nenhum outro
  SocialAccount ativo): exige no payload `email` (vira o email principal)
  + `password` ou flag `request_password_reset` — o front coleta antes de
  confirmar. Caso contrário 400 explicando.

### 6. LGPD/auditoria

- Todo evento grava `AuthEvent` com `summary` legível ("Vinculou conta
  Microsoft pmoro@aluno.fiocruz.br").
- Desvínculo não apaga dados — a conta continua do usuário; o IdP era só
  a chave de entrada.

## Arquivos a tocar

- `accounts/models.py` — `SocialAccount`, `AuthEvent`
- `accounts/services/oauth.py` — resolução de `sub`, helpers de vínculo
- `accounts/views.py` — callbacks (modo link + aviso), endpoints novos
- `accounts/urls.py` — rotas novas
- `accounts/serializers.py` — `SocialAccountSerializer`, payloads
- `accounts/migrations/` — migration nova
- `accounts/tests.py` — testes de match por sub, aviso, link, unlink

## Critérios de aceite

- [ ] Login SSO com SocialAccount existente casa por `sub` mesmo se o
      email do IdP mudar
- [ ] Login SSO com email-match sem vínculo passa pelo aviso e só vincula
      após confirmação
- [ ] Perfil lista e desvincula providers; último acesso exige
      email + senha/reset
- [ ] AuthEvent registra login_sso/link/unlink com summary legível
- [ ] Identidade já vinculada a outro usuário retorna 409 (merge é BE-30)
- [ ] `manage.py test` verde; migration expand-contract

## Fora de escopo

- Merge de duas `User` existentes (BE-30)
- UI (FE-31)
- Domain auto-join opt-in por org
- Reautenticação para link (aceitável no beta; registrar como hardening futuro)
