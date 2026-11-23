"""Gmail SMTP로 메일을 보낸다. 계정은 _labbook/.env의 GMAIL_USER, GMAIL_APP_PASSWORD."""
import smtplib
import ssl
from email.message import EmailMessage

from .common import read_env


def send(cfg, subject, text, html):
    env = read_env()
    user, pw = env.get("GMAIL_USER"), env.get("GMAIL_APP_PASSWORD")
    if not user or not pw:
        return False, ".env에 GMAIL_USER / GMAIL_APP_PASSWORD가 없어 발송 건너뜀"
    m = cfg["mail"]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"labbook <{user}>"
    msg["To"] = m["to"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP_SSL(m["smtp_host"], m["smtp_port"], context=ssl.create_default_context(),
                              timeout=60) as s:
            s.login(user, pw.replace(" ", ""))
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        return False, f"메일 발송 실패: {e}"
    return True, f"메일 보냄 → {m['to']}"
