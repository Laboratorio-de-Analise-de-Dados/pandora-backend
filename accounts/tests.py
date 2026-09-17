from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import (
    AuthEvent,
    Invite,
    Membership,
    Organization,
    Role,
    SocialAccount,
    User,
)
from accounts.serializers import get_or_create_default_roles
from accounts.services.oauth import (
    make_link_state,
    make_link_token,
    read_link_state,
    resolve_microsoft_email,
    unique_username_for_email,
)


class InviteAcceptTests(APITestCase):
    def setUp(self):
        self.roles = get_or_create_default_roles()
        self.organization = Organization.objects.create(name="Lab A", org_type="lab")
        self.owner = User.objects.create_user(
            username="owner", email="owner@example.com", password="pass12345"
        )
        Membership.objects.create(
            user=self.owner,
            organization=self.organization,
            role=self.roles[Role.ORG_ADMIN],
            status="active",
        )
        self.guest = User.objects.create_user(
            username="guest", email="guest@example.com", password="pass12345"
        )
        self.invite = Invite.objects.create(
            email="guest@example.com",
            organization=self.organization,
            role=self.roles[Role.MEMBER],
            token="token-guest",
            status="pending",
            expires_at=timezone.now() + timedelta(hours=24),
        )

    def accept(self):
        self.client.force_authenticate(user=self.guest)
        return self.client.post(
            reverse("invite_accept", kwargs={"token": self.invite.token})
        )

    def test_accept_creates_active_membership_in_inviting_organization(self):
        response = self.accept()

        self.assertEqual(response.status_code, 200)

        membership = Membership.objects.get(
            user=self.guest, organization=self.organization
        )
        self.assertEqual(membership.status, "active")
        self.assertEqual(membership.role, self.roles[Role.MEMBER])

        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, "accepted")

    def test_accepted_member_appears_in_organization_list(self):
        self.accept()

        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("organization_list_create"))

        self.assertEqual(response.status_code, 200)
        organization = next(
            org for org in response.data if org["id"] == self.organization.id
        )
        emails = [member["user"]["email"] for member in organization["members"]]
        self.assertIn("guest@example.com", emails)
        self.assertIn("owner@example.com", emails)

    def test_organization_list_hides_inactive_memberships(self):
        self.accept()
        Membership.objects.filter(user=self.guest).update(status="inactive")

        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("organization_list_create"))

        organization = next(
            org for org in response.data if org["id"] == self.organization.id
        )
        emails = [member["user"]["email"] for member in organization["members"]]
        self.assertNotIn("guest@example.com", emails)

    def membership_detail_url(self, membership):
        return reverse(
            "membership_detail",
            kwargs={
                "organization_id": membership.organization_id,
                "pk": membership.id,
            },
        )

    def test_org_admin_changes_member_role_by_name(self):
        self.accept()
        membership = Membership.objects.get(user=self.guest)

        self.client.force_authenticate(user=self.owner)
        response = self.client.patch(
            self.membership_detail_url(membership), {"role": Role.ORG_ADMIN}
        )

        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.role.name, Role.ORG_ADMIN)

    def test_org_admin_removes_member_by_inactivating(self):
        self.accept()
        membership = Membership.objects.get(user=self.guest)

        self.client.force_authenticate(user=self.owner)
        response = self.client.delete(self.membership_detail_url(membership))

        self.assertEqual(response.status_code, 204)
        membership.refresh_from_db()
        self.assertEqual(membership.status, "inactive")

    def test_removed_member_disappears_from_organization_list(self):
        self.accept()
        membership = Membership.objects.get(user=self.guest)

        self.client.force_authenticate(user=self.owner)
        self.client.delete(self.membership_detail_url(membership))
        response = self.client.get(reverse("organization_list_create"))

        emails = [
            member["user"]["email"]
            for org in response.data
            for member in org["members"]
        ]
        self.assertNotIn(self.guest.email, emails)

    def test_member_cannot_manage_memberships(self):
        self.accept()
        membership = Membership.objects.get(user=self.guest)

        self.client.force_authenticate(user=self.guest)
        response = self.client.delete(self.membership_detail_url(membership))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Membership.objects.filter(id=membership.id).exists())

    def test_cannot_remove_last_org_admin(self):
        owner_membership = Membership.objects.get(user=self.owner)

        self.client.force_authenticate(user=self.owner)
        response = self.client.delete(self.membership_detail_url(owner_membership))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Membership.objects.filter(id=owner_membership.id).exists())

    def test_cannot_demote_last_org_admin(self):
        owner_membership = Membership.objects.get(user=self.owner)

        self.client.force_authenticate(user=self.owner)
        response = self.client.patch(
            self.membership_detail_url(owner_membership), {"role": Role.MEMBER}
        )

        self.assertEqual(response.status_code, 400)
        owner_membership.refresh_from_db()
        self.assertEqual(owner_membership.role.name, Role.ORG_ADMIN)

    def test_admin_cannot_manage_membership_of_another_organization(self):
        self.accept()
        other_org = Organization.objects.create(name="Lab B", org_type="lab")
        outsider = User.objects.create_user(
            username="outsider", email="outsider@example.com", password="pass12345"
        )
        foreign = Membership.objects.create(
            user=outsider,
            organization=other_org,
            role=self.roles[Role.MEMBER],
            status="active",
        )

        self.client.force_authenticate(user=self.owner)
        response = self.client.delete(
            reverse(
                "membership_detail",
                kwargs={
                    "organization_id": self.organization.id,
                    "pk": foreign.id,
                },
            )
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Membership.objects.filter(id=foreign.id).exists())

    def test_accept_rejects_invite_of_another_email(self):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="pass12345"
        )
        self.client.force_authenticate(user=other)

        response = self.client.post(
            reverse("invite_accept", kwargs={"token": self.invite.token})
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            Membership.objects.filter(
                user=other, organization=self.organization
            ).exists()
        )


class ResolveMicrosoftEmailTests(SimpleTestCase):
    def test_prefers_graph_mail(self):
        profile = {"mail": "user@org.com", "userPrincipalName": "u@t.onmicrosoft.com"}
        self.assertEqual(resolve_microsoft_email(profile, {}), "user@org.com")

    def test_falls_back_to_other_mails(self):
        profile = {
            "mail": None,
            "otherMails": ["aluno@fiocruz.br"],
            "userPrincipalName": "u@fiocruzbr.onmicrosoft.com",
        }
        self.assertEqual(resolve_microsoft_email(profile, {}), "aluno@fiocruz.br")

    def test_uses_id_token_email(self):
        profile = {"mail": None, "userPrincipalName": "u@t.onmicrosoft.com"}
        claims = {"email": "real@org.com"}
        self.assertEqual(resolve_microsoft_email(profile, claims), "real@org.com")

    def test_uses_id_token_preferred_username(self):
        profile = {"mail": None, "userPrincipalName": "u@t.onmicrosoft.com"}
        claims = {"preferred_username": "real@org.com"}
        self.assertEqual(resolve_microsoft_email(profile, claims), "real@org.com")

    def test_normalizes_ext_guest_format(self):
        profile = {
            "mail": None,
            "userPrincipalName": "paulohenrikk_hotmail.com#EXT#@t.onmicrosoft.com",
        }
        self.assertEqual(
            resolve_microsoft_email(profile, {}), "paulohenrikk@hotmail.com"
        )

    def test_upn_onmicrosoft_as_last_resort(self):
        profile = {"mail": None, "userPrincipalName": "u@t.onmicrosoft.com"}
        self.assertEqual(resolve_microsoft_email(profile, {}), "u@t.onmicrosoft.com")

    def test_returns_none_when_nothing_available(self):
        self.assertIsNone(resolve_microsoft_email({}, {}))


class UniqueUsernameForEmailTests(TestCase):
    def test_uses_email_local_part(self):
        self.assertEqual(unique_username_for_email("pmoro@aluno.fiocruz.br"), "pmoro")

    def test_suffixes_on_collision(self):
        User.objects.create_user(username="pmoro", email="a@x.com", password="x")
        User.objects.create_user(username="pmoro2", email="b@x.com", password="x")
        self.assertEqual(unique_username_for_email("pmoro@fiocruz.br"), "pmoro3")

    def test_sanitizes_invalid_chars(self):
        self.assertEqual(
            unique_username_for_email("paulo henrique@x.com"), "paulo_henrique"
        )


def _callback_params(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


@override_settings(GOOGLE_AUTH_ENABLED=True, FRONTEND_URL="http://localhost:3000")
class SocialLoginFlowTests(APITestCase):
    """BE-29: dispatch do callback OAuth — sub-match, aviso de vínculo,
    usuário novo e modo link."""

    def _set_google_state(self):
        session = self.client.session
        session["google_auth_state"] = "state-ok"
        session.save()

    def _google_callback(self, identity, state="state-ok"):
        self._set_google_state()
        with patch("accounts.views.google_fetch_identity", return_value=identity):
            return self.client.get(
                reverse("google_auth_callback"),
                {"code": "code", "state": state},
            )

    def test_login_by_sub_ignores_changed_email(self):
        user = User.objects.create_user(
            username="pmoro", email="pmoro@aluno.fiocruz.br", password="x"
        )
        SocialAccount.objects.create(
            user=user,
            provider="google",
            provider_user_id="sub-1",
            email="antigo@fiocruz.br",
        )

        response = self._google_callback(
            {"sub": "sub-1", "email": "novo@fiocruz.br", "name": "Paulo"}
        )

        self.assertEqual(response.status_code, 302)
        params = _callback_params(response.url)
        self.assertEqual(params["user_id"], str(user.id))
        self.assertIn("access", params)
        event = AuthEvent.objects.get(user=user)
        self.assertEqual(event.action, "login_sso")

    def test_email_match_redirects_to_link_notice(self):
        User.objects.create_user(
            username="pmoro", email="pmoro@aluno.fiocruz.br", password="x"
        )

        response = self._google_callback(
            {"sub": "sub-novo", "email": "pmoro@aluno.fiocruz.br", "name": "P"}
        )

        params = _callback_params(response.url)
        self.assertEqual(params["link_notice"], "1")
        self.assertEqual(params["provider"], "google")
        self.assertNotIn("access", params)
        self.assertFalse(
            SocialAccount.objects.filter(provider_user_id="sub-novo").exists()
        )

    def test_new_user_creates_social_account(self):
        response = self._google_callback(
            {"sub": "sub-new", "email": "novo@exemplo.com", "name": "Novo"}
        )

        params = _callback_params(response.url)
        self.assertIn("access", params)
        user = User.objects.get(email="novo@exemplo.com")
        self.assertTrue(
            SocialAccount.objects.filter(
                user=user, provider="google", provider_user_id="sub-new", active=True
            ).exists()
        )
        self.assertFalse(user.has_usable_password())

    def test_confirm_link_issues_tokens_and_creates_account(self):
        user = User.objects.create_user(
            username="pmoro", email="pmoro@aluno.fiocruz.br", password="x"
        )
        token = make_link_token("google", "sub-1", user.email, user.id)

        response = self.client.post(
            reverse("social_confirm_link", kwargs={"provider": "google"}),
            {"token": token},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        account = SocialAccount.objects.get(provider_user_id="sub-1")
        self.assertEqual(account.user, user)
        self.assertTrue(AuthEvent.objects.filter(user=user, action="link").exists())

    def test_confirm_link_rejects_identity_of_another_user(self):
        User.objects.create_user(
            username="pmoro", email="pmoro@fiocruz.br", password="x"
        )
        other = User.objects.create_user(
            username="other", email="other@x.com", password="x"
        )
        SocialAccount.objects.create(
            user=other,
            provider="google",
            provider_user_id="sub-1",
            email="other@x.com",
        )
        token = make_link_token("google", "sub-1", "pmoro@fiocruz.br", other.id + 999)

        # token aponta para user que não existe → 404
        response = self.client.post(
            reverse("social_confirm_link", kwargs={"provider": "google"}),
            {"token": token},
        )
        self.assertEqual(response.status_code, 404)

        # conflito real: sub já é de `other`, token mirando `pmoro`
        pmoro = User.objects.get(username="pmoro")
        token = make_link_token("google", "sub-1", pmoro.email, pmoro.id)
        response = self.client.post(
            reverse("social_confirm_link", kwargs={"provider": "google"}),
            {"token": token},
        )
        self.assertEqual(response.status_code, 409)

    def test_confirm_link_rejects_tampered_or_wrong_provider(self):
        user = User.objects.create_user(
            username="pmoro", email="pmoro@fiocruz.br", password="x"
        )
        response = self.client.post(
            reverse("social_confirm_link", kwargs={"provider": "google"}),
            {"token": "token.forged.invalid"},
        )
        self.assertEqual(response.status_code, 400)

        token = make_link_token("microsoft", "sub-x", user.email, user.id)
        response = self.client.post(
            reverse("social_confirm_link", kwargs={"provider": "google"}),
            {"token": token},
        )
        self.assertEqual(response.status_code, 400)

    def test_link_mode_links_identity_to_session_user(self):
        user = User.objects.create_user(
            username="pmoro", email="pmoro@fiocruz.br", password="x"
        )
        link_state = make_link_state(user.id)
        with patch(
            "accounts.views.google_fetch_identity",
            return_value={
                "sub": "sub-link",
                "email": "paulohenrikk@gmail.com",
                "name": "Paulo",
            },
        ):
            response = self.client.get(
                reverse("google_auth_callback"),
                {"code": "code", "state": link_state},
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/profile?linked=google", response.url)
        account = SocialAccount.objects.get(provider_user_id="sub-link")
        self.assertEqual(account.user, user)

    def test_link_mode_conflict_offers_merge(self):
        user = User.objects.create_user(
            username="pmoro", email="pmoro@fiocruz.br", password="x"
        )
        other = User.objects.create_user(
            username="other", email="other@x.com", password="x"
        )
        SocialAccount.objects.create(
            user=other,
            provider="google",
            provider_user_id="sub-1",
            email="other@x.com",
        )
        link_state = make_link_state(user.id)
        with patch(
            "accounts.views.google_fetch_identity",
            return_value={"sub": "sub-1", "email": "other@x.com", "name": "O"},
        ):
            response = self.client.get(
                reverse("google_auth_callback"),
                {"code": "code", "state": link_state},
            )

        params = _callback_params(response.url)
        self.assertEqual(params["merge_notice"], "1")
        self.assertEqual(params["email"], "other@x.com")
        self.assertIn("token", params)

    def test_link_mode_email_match_offers_merge(self):
        """Identidade livre, mas o email já é de outra conta ativa (legada,
        sem SocialAccount) → merge em vez de deixar a conta órfã."""
        user = User.objects.create_user(
            username="pmoro", email="pmoro@fiocruz.br", password="x"
        )
        legacy = User.objects.create_user(
            username="legacy", email="dup@x.com", password="x"
        )
        link_state = make_link_state(user.id)
        with patch(
            "accounts.views.google_fetch_identity",
            return_value={"sub": "sub-free", "email": "dup@x.com", "name": "D"},
        ):
            response = self.client.get(
                reverse("google_auth_callback"),
                {"code": "code", "state": link_state},
            )

        params = _callback_params(response.url)
        self.assertEqual(params["merge_notice"], "1")
        self.assertEqual(params["email"], "dup@x.com")
        # O vínculo já foi criado na conta atual — o merge migra o resto.
        self.assertTrue(
            SocialAccount.objects.filter(
                user=user, provider_user_id="sub-free"
            ).exists()
        )

    def test_link_mode_reclaims_identity_of_merged_account(self):
        user = User.objects.create_user(
            username="pmoro", email="pmoro@fiocruz.br", password="x"
        )
        canonical = User.objects.create_user(
            username="canon", email="canon@x.com", password="x"
        )
        dead = User.objects.create_user(
            username="dead",
            email="dead@x.com",
            password="x",
        )
        dead.is_active = False
        dead.merged_into = canonical
        dead.save()
        orphan = SocialAccount.objects.create(
            user=dead,
            provider="google",
            provider_user_id="sub-dead",
            email="dead@x.com",
        )
        link_state = make_link_state(user.id)
        with patch(
            "accounts.views.google_fetch_identity",
            return_value={"sub": "sub-dead", "email": "dead@x.com", "name": "D"},
        ):
            response = self.client.get(
                reverse("google_auth_callback"),
                {"code": "code", "state": link_state},
            )

        self.assertIn("/profile?linked=google", response.url)
        orphan.refresh_from_db()
        self.assertEqual(orphan.user, user)
        self.assertTrue(orphan.active)

    def test_callback_without_state_is_rejected(self):
        with patch(
            "accounts.views.google_fetch_identity",
            return_value={"sub": "s", "email": "e@x.com", "name": "E"},
        ):
            response = self.client.get(
                reverse("google_auth_callback"), {"code": "code"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email="e@x.com").exists())

    def test_link_init_requires_authentication(self):
        response = self.client.get(reverse("google_auth_link_init"))
        self.assertEqual(response.status_code, 401)

    def test_link_init_returns_signed_authorize_url(self):
        user = User.objects.create_user(
            username="pmoro", email="pmoro@fiocruz.br", password="x"
        )
        self.client.force_authenticate(user=user)
        response = self.client.get(reverse("google_auth_link_init"))

        self.assertEqual(response.status_code, 200)
        state = _callback_params(response.data["authorize_url"])["state"]
        payload = read_link_state(state)
        self.assertEqual(payload["user_id"], user.id)


class SocialAccountManagementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="pmoro", email="pmoro@fiocruz.br", password="pass12345"
        )
        self.client.force_authenticate(user=self.user)

    def _link(self, provider="google", sub="sub-1", email="pmoro@fiocruz.br"):
        return SocialAccount.objects.create(
            user=self.user, provider=provider, provider_user_id=sub, email=email
        )

    def test_lists_only_own_active_accounts(self):
        self._link()
        self._link(provider="microsoft", sub="oid-9")
        other = User.objects.create_user(
            username="other", email="o@x.com", password="x"
        )
        SocialAccount.objects.create(
            user=other,
            provider="google",
            provider_user_id="sub-x",
            email="o@x.com",
        )
        inactive = self._link(provider="google", sub="sub-old")
        inactive.active = False
        inactive.save()

        response = self.client.get(reverse("user_social_accounts"))

        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            {a["provider"] for a in response.data}, {"google", "microsoft"}
        )

    def test_unlink_inactivates_and_logs_event(self):
        account = self._link()
        response = self.client.post(
            reverse("social_account_unlink", kwargs={"pk": account.id})
        )

        self.assertEqual(response.status_code, 200)
        account.refresh_from_db()
        self.assertFalse(account.active)
        self.assertIsNotNone(account.unlinked_at)
        event = AuthEvent.objects.get(user=self.user, action="unlink")
        self.assertIn("google", event.summary)

    def test_unlink_requires_own_account(self):
        other = User.objects.create_user(
            username="other", email="o@x.com", password="x"
        )
        foreign = SocialAccount.objects.create(
            user=other,
            provider="google",
            provider_user_id="sub-x",
            email="o@x.com",
        )
        response = self.client.post(
            reverse("social_account_unlink", kwargs={"pk": foreign.id})
        )
        self.assertEqual(response.status_code, 404)
        foreign.refresh_from_db()
        self.assertTrue(foreign.active)

    def test_unlink_last_access_requires_credential_setup(self):
        sso_user = User.objects.create_user(username="sso", email="sso@x.com")
        sso_user.set_unusable_password()
        sso_user.save()
        account = SocialAccount.objects.create(
            user=sso_user,
            provider="google",
            provider_user_id="s1",
            email="sso@x.com",
        )
        self.client.force_authenticate(user=sso_user)

        response = self.client.post(
            reverse("social_account_unlink", kwargs={"pk": account.id})
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.data["requires_credential_setup"])
        account.refresh_from_db()
        self.assertTrue(account.active)

    def test_unlink_last_access_with_new_password(self):
        sso_user = User.objects.create_user(username="sso", email="sso@x.com")
        sso_user.set_unusable_password()
        sso_user.save()
        account = SocialAccount.objects.create(
            user=sso_user,
            provider="google",
            provider_user_id="s1",
            email="sso@x.com",
        )
        self.client.force_authenticate(user=sso_user)

        response = self.client.post(
            reverse("social_account_unlink", kwargs={"pk": account.id}),
            {"password": "novasenha123"},
        )

        self.assertEqual(response.status_code, 200)
        sso_user.refresh_from_db()
        self.assertTrue(sso_user.check_password("novasenha123"))
        account.refresh_from_db()
        self.assertFalse(account.active)

    def test_unlink_allowed_with_password_and_other_provider(self):
        # tem senha utilizável — não precisa de payload extra
        self._link()
        second = self._link(provider="microsoft", sub="oid-9")
        response = self.client.post(
            reverse("social_account_unlink", kwargs={"pk": second.id})
        )
        self.assertEqual(response.status_code, 200)

    def test_local_login_logs_auth_event(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": "pmoro", "password": "pass12345"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            AuthEvent.objects.filter(user=self.user, action="login_local").exists()
        )


class AccountMergeTests(APITestCase):
    """BE-30: merge de contas — identidade do IdP já vinculada a outra
    conta oferece fundir na conta logada."""

    def setUp(self):
        self.roles = get_or_create_default_roles()
        self.canonical = User.objects.create_user(
            username="canon", email="canon@x.com", password="pass12345"
        )
        self.absorbed = User.objects.create_user(
            username="abs", email="abs@x.com", password="pass12345"
        )
        self.client.force_authenticate(user=self.canonical)

    def _confirm(self, token):
        return self.client.post(reverse("merge_confirm"), {"token": token})

    def test_confirm_merge_migrates_everything(self):
        from accounts.services.merge import make_merge_token
        from fcs_parser.models import ExperimentModel

        org = Organization.objects.create(name="Lab", org_type="lab")
        Membership.objects.create(
            user=self.absorbed,
            organization=org,
            role=self.roles[Role.MEMBER],
            status="active",
        )
        SocialAccount.objects.create(
            user=self.absorbed,
            provider="microsoft",
            provider_user_id="oid-1",
            email="abs@x.com",
        )
        experiment = ExperimentModel.objects.create(
            title="Exp", created_by=self.absorbed
        )

        token = make_merge_token("microsoft", "oid-1", self.absorbed.id)
        response = self._confirm(token)

        self.assertEqual(response.status_code, 200)
        self.absorbed.refresh_from_db()
        self.assertFalse(self.absorbed.is_active)
        self.assertEqual(self.absorbed.merged_into, self.canonical)
        self.assertEqual(
            SocialAccount.objects.get(provider_user_id="oid-1").user,
            self.canonical,
        )
        self.assertEqual(
            Membership.objects.get(user=self.canonical, organization=org).status,
            "active",
        )
        experiment.refresh_from_db()
        self.assertEqual(experiment.created_by, self.canonical)
        self.assertTrue(
            AuthEvent.objects.filter(
                user=self.canonical, action="merge", target_user=self.absorbed
            ).exists()
        )

    def test_merge_keeps_stronger_role_on_conflict(self):
        from accounts.services.merge import make_merge_token

        org = Organization.objects.create(name="Lab", org_type="lab")
        Membership.objects.create(
            user=self.canonical,
            organization=org,
            role=self.roles[Role.MEMBER],
            status="active",
        )
        Membership.objects.create(
            user=self.absorbed,
            organization=org,
            role=self.roles[Role.ORG_ADMIN],
            status="active",
        )

        token = make_merge_token("google", "sub-1", self.absorbed.id)
        self._confirm(token)

        membership = Membership.objects.get(user=self.canonical, organization=org)
        self.assertEqual(membership.role.name, Role.ORG_ADMIN)
        absorbed_membership = Membership.objects.get(user=self.absorbed)
        self.assertEqual(absorbed_membership.status, "inactive")

    def test_confirm_merge_requires_auth_and_valid_token(self):
        self.client.force_authenticate(user=None)
        response = self._confirm("x")
        self.assertEqual(response.status_code, 401)

        self.client.force_authenticate(user=self.canonical)
        response = self._confirm("token.forged.invalid")
        self.assertEqual(response.status_code, 400)

    def test_confirm_merge_rejects_self_and_already_merged(self):
        from accounts.services.merge import make_merge_token

        token = make_merge_token("google", "sub-1", self.canonical.id)
        self.assertEqual(self._confirm(token).status_code, 400)

        self.absorbed.is_active = False
        self.absorbed.save()
        token = make_merge_token("google", "sub-1", self.absorbed.id)
        self.assertEqual(self._confirm(token).status_code, 400)

    def test_merged_account_cannot_login(self):
        from accounts.services.merge import make_merge_token

        token = make_merge_token("google", "sub-1", self.absorbed.id)
        self._confirm(token)

        self.client.force_authenticate(user=None)
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": "abs", "password": "pass12345"},
        )
        self.assertEqual(response.status_code, 401)
