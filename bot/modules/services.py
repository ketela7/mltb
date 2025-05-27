from time import time

from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.button_build import ButtonMaker
from ..helper.telegram_helper.message_utils import MessageUtils
from ..helper.telegram_helper.filters import CustomFilters
from ..helper.telegram_helper.bot_commands import BotCommands


@new_task
async def start(_, message):
    mu = MessageUtils(message)
    buttons = ButtonMaker()
    buttons.url_button(
        "Repo", "https://www.github.com/anasty17/mirror-leech-telegram-bot"
    )
    buttons.url_button("Code Owner", "https://t.me/anas_tayyar")
    reply_markup = buttons.build_menu(2)
    if await CustomFilters.authorized(_, message):
        start_string = f"""
This bot can mirror from links|tgfiles|torrents|nzb|rclone-cloud to any rclone cloud, Google Drive or to telegram.
Type /{BotCommands.HelpCommand[0]} to get a list of available commands
"""
        await mu.reply(start_string, reply_markup)
    else:
        await mu.reply(
            "This bot can mirror from links|tgfiles|torrents|nzb|rclone-cloud to any rclone cloud, Google Drive or to telegram.\n\n⚠️ You Are not authorized user! Deploy your own mirror-leech bot",
            reply_markup,
        )


@new_task
async def ping(_, message):
    mu = MessageUtils(message)
    start_time = int(round(time() * 1000))
    reply = await mu.reply("Starting Ping")
    end_time = int(round(time() * 1000))
    await mu.edit(f"{end_time - start_time} ms", message=reply)


@new_task
async def log(_, message):
    await MessageUtils(message).reply(document="log.txt")
