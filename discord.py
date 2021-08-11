import logging
import os

from dhooks import Embed, Webhook

DISCORD_WARNING_SENT = False
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')
DISCORD_WEBHOOK_INFO_URL = os.environ.get('DISCORD_WEBHOOK_INFO_URL', DISCORD_WEBHOOK_URL)
DISCORD_WEBHOOK_WARNING_URL = os.environ.get('DISCORD_WEBHOOK_WARNING_URL', DISCORD_WEBHOOK_URL)
DISCORD_WEBHOOK_ERROR_URL = os.environ.get('DISCORD_WEBHOOK_ERROR_URL', DISCORD_WEBHOOK_URL)


def message(severity, message, include_icon=True):
    """Send a simple text message to discord.
    """
    global DISCORD_WARNING_SENT

    severity_icon, discord_url = severities[severity]
    if include_icon:
        message = severity_icon + ' ' + message

    if not discord_url or discord_url == 'none':
        if not DISCORD_WARNING_SENT:
            DISCORD_WARNING_SENT = True
            logging.warning('DISCORD_WEBHOOK_URL not configured, will not send messages to discord.')
        logging.info('Discord message not sent: %s', message)
        return

    try:
        discord = Webhook(discord_url)
        discord.send(message)
    except Exception as e:
        logging.error('Unhandled exception when sending discord message:')
        logging.exception(e)


def embed(severity, source, title, description=None, **fields):
    """Send an embedded message to discord.
    """
    global DISCORD_WARNING_SENT

    severity_icon, discord_url = severities[severity]

    if not discord_url or discord_url == 'none':
        if not DISCORD_WARNING_SENT:
            DISCORD_WARNING_SENT = True
            logging.warning('DISCORD_WEBHOOK_URL not configured, will not send messages to discord.')
        logging.info('Discord embed not sent: %s: %s: %s', title, description, fields)
        return

    try:
        discord = Webhook(discord_url)
        title = severity_icon + ' ' + title
        embed = Embed(title=title, description=description, color=0xff0000, timestamp='now')
        embed.set_author(source)
        for field, value in fields.items():
            embed.add_field(field, value)
        discord.send(embed=embed)
    except Exception as e:
        logging.error('Unhandled exception when sending discord embed:')
        logging.exception(e)
