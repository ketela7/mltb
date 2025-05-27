from asyncio import sleep

from .. import task_dict, task_dict_lock, user_data, multi_tags
from ..core.mltb_client import Config
from ..helper.ext_utils.bot_utils import new_task
from ..helper.ext_utils.status_utils import (
    get_task_by_gid,
    get_all_tasks,
    MirrorStatus,
)
from ..helper.telegram_helper import button_build
from ..helper.telegram_helper.bot_commands import BotCommands
from ..helper.telegram_helper.filters import CustomFilters
from ..helper.telegram_helper.message_utils import MessageUtils
    


@new_task
async def cancel(_, message):
    mu = MessageUtils(message)
    user_id = message.from_user.id if message.from_user else message.sender_chat.id
    msg = message.text.split()
    if len(msg) > 1:
        gid = msg[1]
        if len(gid) == 4:
            multi_tags.discard(gid)
            return
        else:
            task = await get_task_by_gid(gid)
            if task is None:
                await mu.reply(f"GID: <code>{gid}</code> Not Found.")
                return
    elif reply_to_id := message.reply_to_message_id:
        async with task_dict_lock:
            task = task_dict.get(reply_to_id)
        if task is None:
            await mu.reply("This is not an active task!")
            return
    elif len(msg) == 1:
        msg = (
            "Reply to an active Command message which was used to start the download"
            f" or send <code>/{BotCommands.CancelTaskCommand[0]} GID</code> to cancel it!"
        )
        await mu.reply(msg)
        return
    if (
        Config.OWNER_ID != user_id
        and task.listener.user_id != user_id
        and (user_id not in user_data or not user_data[user_id].get("SUDO"))
    ):
        await mu.reply("This task is not for you!")
        return
    obj = task.task()
    await obj.cancel_task()


@new_task
async def cancel_multi(_, query):
    data = query.data.split()
    user_id = query.from_user.id
    if user_id != int(data[1]) and not await CustomFilters.sudo("", query):
        await query.answer("Not Yours!", show_alert=True)
        return
    tag = int(data[2])
    if tag in multi_tags:
        multi_tags.discard(int(data[2]))
        msg = "Stopped!"
    else:
        msg = "Already Stopped/Finished!"
    await query.answer(msg, show_alert=True)
    await MessageUtils(query.message).delete()


async def cancel_all(status, user_id):
    matches = await get_all_tasks(status.strip(), user_id)
    if not matches:
        return False
    for task in matches:
        obj = task.task()
        await obj.cancel_task()
        await sleep(2)
    return True


def create_cancel_buttons(is_sudo, user_id=""):
    buttons = button_build.ButtonMaker()
    buttons.data_button(
        "Downloading", f"canall ms {MirrorStatus.STATUS_DOWNLOAD} {user_id}"
    )
    buttons.data_button(
        "Uploading", f"canall ms {MirrorStatus.STATUS_UPLOAD} {user_id}"
    )
    buttons.data_button("Seeding", f"canall ms {MirrorStatus.STATUS_SEED} {user_id}")
    buttons.data_button("Spltting", f"canall ms {MirrorStatus.STATUS_SPLIT} {user_id}")
    buttons.data_button("Cloning", f"canall ms {MirrorStatus.STATUS_CLONE} {user_id}")
    buttons.data_button(
        "Extracting", f"canall ms {MirrorStatus.STATUS_EXTRACT} {user_id}"
    )
    buttons.data_button(
        "Archiving", f"canall ms {MirrorStatus.STATUS_ARCHIVE} {user_id}"
    )
    buttons.data_button(
        "QueuedDl", f"canall ms {MirrorStatus.STATUS_QUEUEDL} {user_id}"
    )
    buttons.data_button(
        "QueuedUp", f"canall ms {MirrorStatus.STATUS_QUEUEUP} {user_id}"
    )
    buttons.data_button(
        "SampleVideo", f"canall ms {MirrorStatus.STATUS_SAMVID} {user_id}"
    )
    buttons.data_button(
        "ConvertMedia", f"canall ms {MirrorStatus.STATUS_CONVERT} {user_id}"
    )
    buttons.data_button("FFmpeg", f"canall ms {MirrorStatus.STATUS_FFMPEG} {user_id}")
    buttons.data_button("Paused", f"canall ms {MirrorStatus.STATUS_PAUSED} {user_id}")
    buttons.data_button("All", f"canall ms All {user_id}")
    if is_sudo:
        if user_id:
            buttons.data_button("All Added Tasks", f"canall bot ms {user_id}")
        else:
            buttons.data_button("My Tasks", f"canall user ms {user_id}")
    buttons.data_button("Close", f"canall close ms {user_id}")
    return buttons.build_menu(2)


@new_task
async def cancel_all_buttons(_, message):
    mu = MessageUtils(message)
    async with task_dict_lock:
        count = len(task_dict)
    if count == 0:
        await mu.reply("No active tasks!")
        return
    is_sudo = await CustomFilters.sudo("", message)
    button = create_cancel_buttons(is_sudo, message.from_user.id)
    can_msg = await mu.reply("Choose tasks to cancel!", button)
    await mu.delete(can_msg, wait=True)


@new_task
async def cancel_all_update(_, query):
    data = query.data.split()
    message = query.message
    mu = MessageUtils(message)
    reply_to = message.reply_to_message
    user_id = int(data[3]) if len(data) > 3 else ""
    is_sudo = await CustomFilters.sudo("", query)
    if not is_sudo and user_id and user_id != query.from_user.id:
        await query.answer("Not Yours!", show_alert=True)
    else:
        await query.answer()
    if data[1] == "close":
        await mu.delete(reply_to=True)
    elif data[1] == "back":
        button = create_cancel_buttons(is_sudo, user_id)
        await mu.edit("Choose tasks to cancel!", button)
    elif data[1] == "bot":
        button = create_cancel_buttons(is_sudo, "")
        await mu.edit("Choose tasks to cancel!", button)
    elif data[1] == "user":
        button = create_cancel_buttons(is_sudo, query.from_user.id)
        await mu.edit("Choose tasks to cancel!", button)
    elif data[1] == "ms":
        buttons = button_build.ButtonMaker()
        buttons.data_button("Yes!", f"canall {data[2]} confirm {user_id}")
        buttons.data_button("Back", f"canall back confirm {user_id}")
        buttons.data_button("Close", f"canall close confirm {user_id}")
        button = buttons.build_menu(2)
        await mu.edit(
           f"Are you sure you want to cancel all {data[2]} tasks", button
        )
    else:
        button = create_cancel_buttons(is_sudo, user_id)
        await mu.edit("Choose tasks to cancel.", button)
        res = await cancel_all(data[1], user_id)
        if not res:
            await mu.reply(f"No matching tasks for {data[1]}!", message=reply_to)
