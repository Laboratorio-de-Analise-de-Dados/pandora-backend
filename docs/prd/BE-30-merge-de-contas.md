# BE-30 — Merge de contas de usuário

**Repo:** pandora-backend · **Tipo:** feature · **Base:** `main`
**Branch:** `feat/account-merge`
**Status:** implementado.
**ADR:** `docs/adr/0025` · **Depende de:** BE-29

## Problema

Mesmo com vinculação (BE-29) já existirão contas duplas criadas antes:
a pessoa que logou com dois IdPs diferentes tem duas `User` com dados
próprios (memberships, autoria de gates, convites). Sem merge, a única
saída é pedir para refazer trabalho na conta certa.

## Escopo

### 1. Gatilho do merge

- Ao vincular um provider que já pertence a outro usuário → em vez de 409
  definitivo, oferecer merge: "existe uma conta com esse login — fundir
  em <email atual>?"
- Confirmação explícita obrigatória — nunca automático (mesmo espírito dos
  dry-runs de propagação/restore).

### 2. O que migra para a conta canônica

- `Membership` (conflito de mesmo org: prevalece o papel mais forte)
- Autoria: gates (`author`), `AnalysisRevision.user`, invites enviados/
  recebidos, experimentos criados
- `SocialAccount` da conta absorvida passa para a canônica
- Email da conta absorvida entra como secundário (decisão de modelo:
  `User.emails` adicionais ou apenas histórico no `AuthEvent` — fechar no
  desenho)

### 3. Conta absorvida

- Nunca deleta (invariante): `is_active=False` + marcação de merge
  (ex.: `merged_into` FK) — logins futuros nela redirecionam/barram.
- `AuthEvent` com `action="merge"` registra origem, destino e resumo.

### 4. LGPD

- Merge exige o usuário autenticado na conta canônica + posse do IdP da
  outra conta (o fluxo de link já prova posse).
- Log preserva os dois lados para auditoria.

## Arquivos a tocar

- `accounts/models.py` — `merged_into` no User (ou equivalente)
- `accounts/services/` — `merge_accounts(canonical, absorbed)`
- `accounts/views.py` — endpoint de confirmação
- `accounts/tests.py` — matriz de migração de FKs

## Critérios de aceite

- [ ] Merge migra memberships, autoria e vínculos sem perda
- [ ] Conta absorvida fica inativa e rastreável
- [ ] Conflito de papel no mesmo org resolve determinístico
- [ ] AuthEvent registra o merge
- [ ] Nada é deletado

## Fora de escopo

- Merge iniciado por admin/suporte
- Desfazer merge (se pedir, é decisão nova — restore point de conta)
