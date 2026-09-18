import secrets
import logging
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from accounts.models import Invite, Organization, Role

logger = logging.getLogger(__name__)


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


def send_invite_email(invite: Invite) -> bool:
    # Sem token no link (decisão de segurança): o aceite acontece no sino
    # depois do login. A URL base vai no corpo senão o convidado não sabe
    # onde fica o app.
    app_url = settings.FRONTEND_URL.rstrip("/")
    subject = (
        f"Você foi convidado para entrar na organização {invite.organization.name}"
    )
    text_content = (
        f"Você foi convidado para participar da organização {invite.organization.name}\n"
        f"como {invite.role.name}.\n\n"
        f"Para aceitar, acesse {app_url} com este email e confirme o convite "
        f"no sino de notificações.\n"
        f"Se ainda não tem conta, cadastre-se com este mesmo email."
    )
    html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #2c3e50;">Você foi convidado para <strong>{invite.organization.name}</strong></h2>
            <p>Você foi convidado para participar da organização <strong>{invite.organization.name}</strong>
            como <strong>{invite.role.name}</strong>.</p>
            <p>Para aceitar o convite, acesse o Pandora com este email e confirme o convite no sino de notificações.</p>
            <p style="margin:20px 0;">
                <a href="{app_url}" style="background-color:#4CAF50; color:white; padding:10px 15px; text-decoration:none; border-radius:5px;">
                    Acessar o Pandora
                </a>
            </p>
            <p>Se ainda não tem conta, cadastre-se usando este mesmo email.</p>
        </body>
        </html>
    """

    msg = EmailMultiAlternatives(
        subject, text_content, settings.DEFAULT_FROM_EMAIL, [invite.email]
    )
    msg.attach_alternative(html_content, "text/html")
    try:
        msg.send()
        return True
    except Exception:
        logger.exception("Falha ao enviar email de convite")
        return False


def send_password_reset_email(user, reset_link: str) -> bool:
    subject = "Recuperação de senha - Pandora"
    text_content = (
        f"Olá, {user.username}.\n\n"
        f"Você solicitou a redefinição de senha. Clique no link abaixo:\n{reset_link}\n\n"
        f"Se não foi você, ignore este email."
    )
    html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #2c3e50;">Recuperação de senha</h2>
            <p>Olá, <strong>{user.username}</strong>.</p>
            <p>Você solicitou a redefinição de senha. Clique no botão abaixo:</p>
            <p style="margin:20px 0;">
                <a href="{reset_link}" style="background-color:#4CAF50; color:white; padding:10px 15px; text-decoration:none; border-radius:5px;">
                    Redefinir senha
                </a>
            </p>
            <p>Se não foi você, ignore este email.</p>
        </body>
        </html>
    """

    msg = EmailMultiAlternatives(
        subject, text_content, settings.DEFAULT_FROM_EMAIL, [user.email]
    )
    msg.attach_alternative(html_content, "text/html")
    try:
        msg.send()
        return True
    except Exception:
        logger.exception("Falha ao enviar email de recuperação")
        return False
