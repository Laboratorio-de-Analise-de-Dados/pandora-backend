# accounts/services/email_service.py
import secrets
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from accounts.models import Invite, Organization, Role

def generate_token() -> str:
    return secrets.token_urlsafe(32)

def create_invite(email: str, organization: Organization, role: Role) -> Invite:
    token = generate_token()
    invite = Invite.objects.create(
        email=email,
        organization=organization,
        role=role,
        token=token,
        status="pending",
    )
    return invite

def send_invite_email(invite: Invite) -> None:
    link = f"{settings.FRONTEND_URL}/invite/{invite.token}"
    subject = f"Convite para entrar na organização {invite.organization.name}"
    text_content = (
        f"Você foi convidado para entrar na organização {invite.organization.name}.\n"
        f"Aceite o convite clicando no link: {link}\n\n"
        f"Se não tiver conta, você poderá criar após clicar."
    )
    html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #2c3e50;">Convite para entrar na organização <strong>{invite.organization.name}</strong></h2>
            <p>Você foi convidado para participar da organização <strong>{invite.organization.name}</strong>.</p>
            <p style="margin:20px 0;">
                <a href="{link}" style="background-color:#4CAF50; color:white; padding:10px 15px; text-decoration:none; border-radius:5px;">
                    Aceitar convite
                </a>
            </p>
            <p>Se não tiver conta, você poderá criar após clicar.</p>
        </body>
        </html>
    """

    msg = EmailMultiAlternatives(subject, text_content, settings.DEFAULT_FROM_EMAIL, [invite.email])
    msg.attach_alternative(html_content, "text/html")
    msg.send()
    link = f"{settings.FRONTEND_URL}/invite/{invite.token}"
    subject = f"Convite para entrar na organização {invite.organization.name}"
    text_content = (
        f"Você foi convidado para entrar na organização {invite.organization.name}.\n"
        f"Aceite o convite clicando no link: {link}\n\n"
        f"Se não tiver conta, você poderá criar após clicar."
    )
    html_content = f"""
        <p>Você foi convidado para entrar na organização <strong>{invite.organization.name}</strong>.</p>
        <p><a href="{link}">Clique aqui para aceitar o convite</a></p>
        <p>Se não tiver conta, você poderá criar após clicar.</p>
    """

    msg = EmailMultiAlternatives(subject, text_content, settings.DEFAULT_FROM_EMAIL, [invite.email])
    msg.attach_alternative(html_content, "text/html")
    msg.send()