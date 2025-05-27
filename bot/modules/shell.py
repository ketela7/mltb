from io import BytesIO

from .. import LOGGER
from ..helper.ext_utils.bot_utils import cmd_exec, new_task
from ..helper.telegram_helper.message_utils import MessageUtils


@new_task
async def run_shell(_, message):
    mu = MessageUtils(message)
    cmd = message.text.split(maxsplit=1)
    if len(cmd) == 1:
        await mu.reply("No command to execute was given.")
        return
    cmd = cmd[1]
    stdout, stderr, _ = await cmd_exec(cmd, shell=True)
    reply = ""
    if len(stdout) != 0:
        reply += f"*Stdout*\n<code>{stdout}</code>\n"
        LOGGER.info(f"Shell - {cmd} - {stdout}")
    if len(stderr) != 0:
        reply += f"*Stderr*\n<code>{stderr}</code>"
        LOGGER.error(f"Shell - {cmd} - {stderr}")
    if len(reply) > 3000:
        with BytesIO(str.encode(reply)) as out_file:
            out_file.name = "shell_output.txt"
            await mu.reply(document=out_file)
    elif len(reply) != 0:
        await mu.reply(reply)
    else:
        await mu.reply("No Reply")
