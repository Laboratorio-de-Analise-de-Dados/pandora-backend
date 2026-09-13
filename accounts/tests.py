from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Invite, Membership, Organization, Role, User
from accounts.serializers import get_or_create_default_roles


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
