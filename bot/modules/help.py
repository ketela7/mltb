from ..helper.ext_utils.bot_utils import COMMAND_USAGE, new_task
from ..helper.ext_utils.help_messages import (
    YT_HELP_DICT,
    MIRROR_HELP_DICT,
    CLONE_HELP_DICT,
)
from ..helper.telegram_helper.button_build import ButtonMaker
from ..helper.telegram_helper.message_utils import MessageUtils
from ..helper.ext_utils.help_messages import help_string


@new_task
async def arg_usage(_, query):
    data = query.data.split()
    message = query.message
    mu = MessageUtils(message)
    if data[1] == "close":
        await mu.delete()
    elif data[1] == "back":
        if data[2] == "m":
            await edit_message(
                message, COMMAND_USAGE["mirror"][0], COMMAND_USAGE["mirror"][1]
            )
        elif data[2] == "y":
            await mu.edit(COMMAND_USAGE["yt"][0], COMMAND_USAGE["yt"][1])
        elif data[2] == "c":
            await mu.edit(
                COMMAND_USAGE["clone"][0], COMMAND_USAGE["clone"][1]
            )
    elif data[1] == "mirror":
        buttons = ButtonMaker()
        buttons.data_button("Back", "help back m")
        button = buttons.build_menu()
        await mu.edit(MIRROR_HELP_DICT[data[2]], button)
    elif data[1] == "yt":
        buttons = ButtonMaker()
        buttons.data_button("Back", "help back y")
        button = buttons.build_menu()
        await mu.edit(YT_HELP_DICT[data[2]], button)
    elif data[1] == "clone":
        buttons = ButtonMaker()
        buttons.data_button("Back", "help back c")
        button = buttons.build_menu()
        await mu.edit(CLONE_HELP_DICT[data[2]], button)


@new_task
async def bot_help(_, message):
    await MessageUtils(message).reply(help_string)
