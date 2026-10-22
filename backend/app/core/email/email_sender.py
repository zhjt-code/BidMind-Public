"""SMTP 邮件发送

职责: 通过 SMTP 发送事务性邮件(当前用于邮箱验证)
不做: 邮件内容业务编排/令牌管理/用户验证状态变更(归 AuthService)

未配置 SMTP(host/user/password 任一为空)时降级为仅记录日志、返回 False、不抛错,
便于"开关开启但 SMTP 未就绪"的灰度暗部署不影响主流程。同步 smtplib 调用经
asyncio.to_thread 卸载到线程, 避免阻塞事件循环。
"""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from loguru import logger

from app.core.config import settings


def _smtp_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD)


def _send_sync(to_email: str, subject: str, body_text: str, body_html: str | None) -> None:
    """同步发送(在线程中执行)"""
    sender = settings.SMTP_FROM or settings.SMTP_USER
    msg = EmailMessage()
    msg["From"] = f"{settings.SMTP_FROM_NAME} <{sender}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    if settings.SMTP_USE_SSL:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=context, timeout=15) as server:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            if settings.SMTP_USE_TLS:
                server.starttls(context=ssl.create_default_context())
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)


async def send_email(
    to_email: str, subject: str, body_text: str, body_html: str | None = None
) -> bool:
    """异步发送邮件。

    Returns:
        True 已提交发送; False 未配置或发送失败(已记录日志, 不抛错)。
    """
    if not _smtp_configured():
        logger.warning(f"SMTP 未配置, 跳过发送邮件: to={to_email}, subject={subject}")
        return False
    try:
        await asyncio.to_thread(_send_sync, to_email, subject, body_text, body_html)
        logger.info(f"邮件已发送: to={to_email}, subject={subject}")
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"邮件发送失败: to={to_email}, err={e}")
        return False


async def send_verification_email(to_email: str, verify_url: str) -> bool:
    """发送邮箱验证邮件"""
    ttl = settings.EMAIL_VERIFY_TOKEN_TTL_HOURS
    subject = "BidMind 邮箱验证"
    text = (
        f"您好,\n\n请点击以下链接完成邮箱验证({ttl}小时内有效):\n{verify_url}\n\n"
        f"如非本人操作, 请忽略本邮件。"
    )
    html = (
        f"<p>您好,</p>"
        f"<p>请点击以下链接完成邮箱验证({ttl}小时内有效):</p>"
        f'<p><a href="{verify_url}">{verify_url}</a></p>'
        f"<p>如非本人操作, 请忽略本邮件。</p>"
    )
    return await send_email(to_email, subject, text, html)


async def send_password_reset_email(to_email: str, reset_url: str) -> bool:
    """发送密码重置邮件"""
    ttl = settings.PASSWORD_RESET_TOKEN_TTL_MINUTES
    subject = "BidMind 密码重置"
    text = (
        f"您好,\n\n我们收到了您的密码重置请求。请点击以下链接设置新密码"
        f"({ttl}分钟内有效):\n{reset_url}\n\n"
        f"如非本人操作, 请忽略本邮件, 您的密码不会被更改。"
    )
    html = (
        f"<p>您好,</p>"
        f"<p>我们收到了您的密码重置请求。请点击以下链接设置新密码({ttl}分钟内有效):</p>"
        f'<p><a href="{reset_url}">{reset_url}</a></p>'
        f"<p>如非本人操作, 请忽略本邮件, 您的密码不会被更改。</p>"
    )
    return await send_email(to_email, subject, text, html)
