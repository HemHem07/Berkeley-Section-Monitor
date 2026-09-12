"""Local Discord credential storage and an explicitly requested delivery test."""
import os
from pathlib import Path
import re
import tempfile

from dotenv import set_key
import requests


def validate(webhook, user_id):
    if not re.fullmatch(r"https://(?:discord\.com|discordapp\.com)/api/webhooks/[0-9]+/[A-Za-z0-9_-]+", webhook):
        raise ValueError("Enter a Discord webhook URL copied from your channel's Integrations settings.")
    if user_id and not re.fullmatch(r"[0-9]{1,20}", user_id):
        raise ValueError("The optional Discord user ID must contain only digits (up to 20).")


def save(path, webhook, user_id):
    """Preserve unrelated dotenv settings; commit both values together."""
    validate(webhook, user_id)
    path = Path(path)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    descriptor, name = tempfile.mkstemp(prefix=".discord-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(original)
        set_key(str(temporary), "NOTIFICATION_WEBHOOK_URL", webhook)
        set_key(str(temporary), "DISCORD_USER_ID", user_id)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def send_test(webhook, user_id):
    validate(webhook, user_id)
    content = "Berkeley Section Monitor — test notification. Discord delivery is working. This is not an enrollment alert."
    if user_id:
        content = f"<@{user_id}> " + content
    try:
        response = requests.post(webhook, params={"wait":"true"}, json={
            "content":content, "allowed_mentions":{"parse":[], "users":[user_id] if user_id else []}
        }, timeout=15, allow_redirects=False)
    except requests.RequestException:
        raise ValueError("Discord test could not be confirmed. Check your connection before retrying.") from None
    if not 200 <= response.status_code < 300:
        raise ValueError(f"Discord rejected the test (HTTP {response.status_code}). Check the webhook or try again later.")
