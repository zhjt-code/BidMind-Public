"""事务性邮件发送子域"""

from app.core.email.email_sender import (
    send_email,
    send_password_reset_email,
    send_verification_email,
)

__all__ = ["send_email", "send_verification_email", "send_password_reset_email"]
