from asyncio import sleep
from io import BytesIO
from re import match as re_match
from time import time

from pyrogram.enums import ChatType
from pyrogram.errors import (
    FloodWait,
    MediaCaptionTooLong,
    MessageAuthorRequired,
    MessageEmpty,
    MessageIdInvalid,
    MessageNotModified,
    MessageTooLong,
    ReplyMarkupInvalid,
)

from ... import DOWNLOAD_DIR, LOGGER, intervals, status_dict, task_dict_lock
from ...core.config_manager import Config
from ...core.mltb_client import TgClient
from ..ext_utils.bot_utils import SetInterval, async_request
from ..ext_utils.exceptions import RssShutdownException, TgLinkException
from ..ext_utils.status_utils import get_readable_message


async def get_tg_link_message(link):
    message = None
    links = []
    if link.startswith("https://t.me/"):
        private = False
        msg = re_match(
            r"https:\/\/t\.me\/(?:c\/)?([^\/]+)(?:\/[^\/]+)?\/([0-9-]+)", link
        )
    else:
        private = True
        msg = re_match(
            r"tg:\/\/openmessage\?user_id=([0-9]+)&message_id=([0-9-]+)", link
        )
        if not TgClient.user:
            raise TgLinkException(
                "USER_SESSION_STRING required for this private link!")

    chat = msg[1]
    msg_id = msg[2]
    if "-" in msg_id:
        start_id, end_id = msg_id.split("-")
        msg_id = start_id = int(start_id)
        end_id = int(end_id)
        btw = end_id - start_id
        if private:
            link = link.split("&message_id=")[0]
            links.append(f"{link}&message_id={start_id}")
            for _ in range(btw):
                start_id += 1
                links.append(f"{link}&message_id={start_id}")
        else:
            link = link.rsplit("/", 1)[0]
            links.append(f"{link}/{start_id}")
            for _ in range(btw):
                start_id += 1
                links.append(f"{link}/{start_id}")
    else:
        msg_id = int(msg_id)

    if chat.isdigit():
        chat = int(chat) if private else int(f"-100{chat}")

    if not private:
        try:
            message = await TgClient.bot.get_messages(chat_id=chat, message_ids=msg_id)
            if message.empty:
                private = True
        except Exception as e:
            private = True
            if not TgClient.user:
                raise e

    if not private:
        return (links, "bot") if links else (message, "bot")
    elif TgClient.user:
        try:
            user_message = await TgClient.user.get_messages(
                chat_id=chat, message_ids=msg_id
            )
        except Exception as e:
            raise TgLinkException(
                f"You don't have access to this chat!. ERROR: {e}"
            ) from e
        if not user_message.empty:
            return (links, "user") if links else (user_message, "user")
    else:
        raise TgLinkException("Private: Please report!")


async def temp_download(msg):
    path = f"{DOWNLOAD_DIR}temp"
    return await msg.download(file_name=f"{path}/")


async def get_chat(chat):
    chat_id = thread_id = None
    if isinstance(chat, int):
        chat_id = chat
    elif "|" in chat:
        chat_id, thread_id = list(
            map(
                lambda x: int(x) if x.lstrip("-").isdigit() else x,
                chat.split("|", 1),
            )
        )
    elif chat.lstrip("-").isdigit():
        chat_id = int(chat)
    return chat_id, thread_id


class TgApi:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}/"

        self.disable_web_page_preview = Config.BOT_DISABLE_WEB_PAGE_PREVIEW
        self.disable_notification = Config.BOT_DISABLE_NOTIFICATION
        self.protect_content = Config.BOT_PROTECT_CONTENT

        self.supports_streaming = Config.BOT_SUPPORTS_STREAMING
        self.has_spoiler = Config.BOT_HAS_SPOILER

    def _url(self, method: str) -> str:
        return self.base_url + method

    def _build_payload(self, **kwargs):
        payload = {
            "parse_mode": "HTML",
            "protect_content": self.protect_content,
            "disable_notification": self.disable_notification,
            "disable_web_page_preview": self.disable_web_page_preview
        }
        payload.update({k: v for k, v in kwargs.items() if v is not None})
        return payload

    async def getMe(self, return_uname=False):
        r = (await async_request(self._url("getMe"))).json()
        if return_uname:
            return r.get('result').get('username')
        return r

    async def sendMessage(self, chat_id, text, **kwargs):
        payload = self._build_payload(chat_id=chat_id, text=text, **kwargs)
        return (await async_request(self._url("sendMessage"), data=payload)).json()

    async def sendPhoto(self, chat_id, photo, caption=None, **kwargs):
        payload = self._build_payload(
            chat_id=chat_id, caption=caption, photo=photo, **kwargs)
        return (await async_request(self._url("sendPhoto"), data=payload)).json()

    async def sendVideo(self, chat_id, video, caption=None, **kwargs):
        payload = self._build_payload(chat_id=chat_id, video=video, caption=caption,
                                      supports_streaming=True, has_spoiler=True, **kwargs)
        return (await async_request(self._url("sendVideo"), data=payload)).json()


class _Pyrogram(TgApi):
    def __init__(self):
        super().__init__(self.bot_token)
        self.buttons = getattr(self.message, "reply_markup", None)
        self.caption = (
            self.message.caption if getattr(self.message, "media", None)
            else getattr(self.message, "text", None)
        )

    async def _smart_reply(self, method: str, media=None, **kwargs):
        reply_func = getattr(self.message, f"reply_{method}")
        if method == "video":
            kwargs.update({
                "supports_streaming": self.supports_streaming,
                "has_spoiler": self.has_spoiler
            })
        elif method == "document":
            kwargs.update({
                "force_document": True
            })

        return await reply_func(
            media,
            quote=True,
            caption=kwargs.get("text"),
            reply_markup=kwargs.get("buttons"),
            disable_notification=self.disable_notification,
            **{k: v for k, v in kwargs.items() if k not in ("text", "buttons")}
        )
        
        
    async def reply(self, text=None, buttons=None, photo=None, video=None,
                    audio=None, document=None, message=None, force=True, doc_name="message.txt"
                    ):
        try:
            message = message or self.message
            if not message:
                return None

            media_map = {
                "photo": photo,
                "video": video,
                "audio": audio,
                "document": document,
            }
            for method, media in media_map.items():
                if media:
                    return await self._smart_reply(method, media, text=text, buttons=buttons)

            return await message.reply(
                text=text,
                quote=True,
                reply_markup=buttons,
                disable_web_page_preview=self.disable_web_page_preview,
                disable_notification=self.disable_notification,
                protect_content=self.protect_content,
            )

        except FloodWait as f:
            LOGGER.warning(f)
            if force:
                await sleep(f.value * 2)
                return await self.reply(**locals())
        except ReplyMarkupInvalid:
            return await self.reply(text=text, buttons=None, photo=photo, video=video,
                                    audio=audio, document=document, message=message, force=force)
        except MessageTooLong:
            with BytesIO(str.encode(text)) as f:
                f.name = doc_name
                return await self.reply(document=f, message=message, force=force)
        except MediaCaptionTooLong:
            return await self.reply(text=text[:1024], photo=photo, video=video,
                                    audio=audio, document=document, message=message, force=force)
        except Exception as e:
            LOGGER.error(e)
            return str(e)

    async def _send_media(self, method: str, media=None, **kwargs):
        send_func = getattr(self.client, f"send_{method}")
        return await send_func(
            chat_id=kwargs["chat_id"],
            reply_to_message_id=kwargs.get("reply_to"),
            reply_markup=kwargs.get("buttons"),
            caption=kwargs.get("text"),
            disable_notification=self.disable_notification,
            protect_content=self.protect_content,
            **({"force_document": True} if method == "document" else {}),
            **({method: media})
        )

    async def send(self, chat_id=None, text=None, buttons=None, photo=None, video=None,
                   audio=None, document=None, reply_to=None, thread_id=None, client=None, force=True
                   ):
        try:
            client = client or self.client
            chat_id = chat_id or self.message.chat.id
            buttons = buttons or self.buttons

            media_map = {
                "photo": photo,
                "video": video,
                "audio": audio,
                "document": document,
            }
            for method, media in media_map.items():
                if media:
                    return await self._send_media(method, media, chat_id=chat_id,
                                                  text=text, buttons=buttons, reply_to=reply_to)

            return await client.send_message(
                chat_id=chat_id,
                text=text,
                message_thread_id=thread_id,
                reply_to_message_id=reply_to,
                reply_markup=buttons,
                disable_web_page_preview=self.disable_web_page_preview,
                disable_notification=self.disable_notification,
                protect_content=self.protect_content
            )

        except FloodWait as f:
            LOGGER.warning(f)
            if force:
                await sleep(f.value * 2)
                return await self.send(**locals())
        except ReplyMarkupInvalid:
            return await self.send(chat_id=chat_id, text=text, buttons=None, photo=photo,
                                   video=video, audio=audio, document=document, reply_to=reply_to,
                                   thread_id=thread_id, client=client, force=force)
        except MessageTooLong:
            with BytesIO(str.encode(text)) as f:
                f.name = "message.txt"
                return await self.send(chat_id=chat_id, document=f, reply_to=reply_to,
                                       thread_id=thread_id,  buttons=buttons, client=client, force=force)
        except MediaCaptionTooLong:
            return await self.send(chat_id=chat_id, text=text[:1024], buttons=buttons,
                                   photo=photo, video=video, audio=audio, document=document,
                                   reply_to=reply_to, thread_id=thread_id, client=client, force=force)
        except Exception as e:
            LOGGER.error(e)
            return str(e)

    async def forward(self, chat_id=None, caption=None, buttons=None,
                      reply_to=None, as_copy=False, client=None, message=None,
                      wait=False, force=True
                      ):
        try:
            message = message or self.message
            if not message or message.empty:
                return None

            chat = chat_id or Config.FORWARD_TO_CHAT
            if not chat:
                raise Exception("FORWARD_TO_CHAT not provided!")

            chat_id, thread_id = await get_chat(chat)

            if Config.SEND_MSG_DELAY and wait:
                await sleep(Config.SEND_MSG_DELAY)

            # client = client or self.client
            buttons = buttons or message.reply_markup
            real_caption = getattr(message, "caption", "") or getattr(
                message, "text", "")
            final_caption = f"{real_caption or ''}{caption or ''}".strip()

            send_func = message.copy if as_copy else message.forward
            _send_func = client.copy_message if as_copy else client.forward_messages

            kwargs = {
                "chat_id": chat_id,
                "caption": final_caption or None,
                "reply_markup": buttons,
                "reply_to_message_id": reply_to,
                "disable_notification": self.disable_notification,
                "protect_content": self.protect_content,
            }
            if client:
                kwargs.pop("caption", None)
                kwargs["from_chat_id"] = message.chat.id

            if client and as_copy:
                kwargs["message_id"] = message.id
                return await _send_func(**kwargs)
            elif client:  # and not as_copy: #client.forward_messages
                kwargs.pop("reply_markup", None)
                kwargs.pop("reply_to_message_id", None)
                kwargs["message_ids"] = message.id
                return await _send_func(**kwargs)
            elif as_copy and message.media:
                return await send_func(**kwargs)
            elif as_copy and message.text:
                return await self.send(chat_id, final_caption, buttons, client=client)
            else:  # message.forward
                return await send_func(chat_id)

        except FloodWait as f:
            if force:
                await sleep(f.value * 2)
                return await self.forward(**locals())
        except Exception as e:
            LOGGER.error(e)
            return str(e)

    async def edit(self, text=None, buttons=None, message=None, force=True):
        message = message or self.message
        if not message:
            return None

        try:
            if not text:
                return await message.edit_reply_markup(buttons)

            return await message.edit(
                text=text,
                reply_markup=buttons,
                disable_web_page_preview=self.disable_web_page_preview,
            )
        except FloodWait as f:
            LOGGER.warning(f)
            if force:
                await sleep(f.value * 2)
                return await self.edit(text, buttons, message)
        except ReplyMarkupInvalid:
            return await self.edit(text, None, message, force)
        except MessageTooLong:
            return await self.edit(text[:4096], buttons, message, force)
        except MessageAuthorRequired:
            return await self.reply(text, buttons, message=message, force=force)
        except (MessageNotModified, MessageEmpty, MessageIdInvalid):
            return None
        except Exception as e:
            LOGGER.error(e)
            return str(e)

    async def delete(self, message=None, reply_to=False, wait=False):

        if Config.AUTO_DELETE_MESSAGE_DURATION and wait:
            await sleep(Config.AUTO_DELETE_MESSAGE_DURATION)

        async def delete(message=None):
            if message:
                if not message.from_user and message.chat.type != ChatType.BOT and TgClient.user:
                    try:
                        await TgClient.user.delete_messages(chat_id=message.chat.id, message_ids=message.id)
                    except:
                        await TgClient.bot.delete_messages(chat_id=message.chat.id, message_ids=message.id)
                else:
                    try:
                        await message.delete()
                    except:
                        pass
            else:
                try:
                    await self.message.delete()
                except:
                    pass

        message = message or self.message
        if message and reply_to:
            if msr := message.reply_to_message:
                if msrr := msr.reply_to_message:
                    # if msrrr := msrr.reply_to_message:
                    #    await delete(msrrr)
                    await delete(msrr)
                await delete(msr)
        await delete(message)

    async def clear_history(chat_id, user_id=0):
        """if user and user_id != 0:
            chat_id = chat_id or self.message.chat.id
            try:
                if str(chat_id).startswith('-100'):
                    await user.delete_user_history(chat_id, user_id)
            except Exception as e:
                LOGGER.error(f"{chat_id} | {user_id} ERROR: {e}")
            return
        """
        if (
            chat_id != Config.LEECH_DUMP_CHAT
            and str(chat_id).startswith('-')
            and TgClient.user
            and Config.CLEAR_HISTORY_AFTER_RESTART
        ):
            try:
                async for message in TgClient.user.get_chat_history(chat_id):
                    await message.delete()
            except Exception as e:
                LOGGER.error(f"[{chat_id}] ERROR: {e}")

    async def sendRss(self, **kwargs):
        chat_id, thread_id = await get_chat(kwargs.get("chat"))
        caption = kwargs.get("caption")
        video = kwargs.get("video")

        if not caption:
            return None

        if (bot_token := Config.BOT_TOKEN_RSS):
            bot = TgApi(bot_token)
            sender = bot.sendVideo if video else bot.sendMessage
            result = await sender(chat_id=chat_id, video=video, caption=caption) if video else await sender(chat_id=chat_id, text=caption)

            if result and (e := result.get('description')) and 'Bad Request' not in e:
                raise RssShutdownException(f'@{await bot.getMe(True)} | ERROR: {e}')
        else:
            sender = self.send
            await sender(
                chat_id, caption, video=video, thread_id=thread_id
            ) if video else await sender(
                chat_id, caption, thread_id=thread_id
            )

    async def getChat(self, chat):
        return await get_chat(chat)

    async def updateStatus(self, sid=0, force=False):
        if intervals["stopAll"]:
            return
        async with task_dict_lock:
            sid = sid or self.message.chat.id
            if not status_dict.get(sid):
                if obj := intervals["status"].get(sid):
                    obj.cancel()
                    del intervals["status"][sid]
                return
            if not force and time() - status_dict[sid]["time"] < 3:
                return
            status_dict[sid]["time"] = time()
            page_no = status_dict[sid]["page_no"]
            status = status_dict[sid]["status"]
            is_user = status_dict[sid]["is_user"]
            page_step = status_dict[sid]["page_step"]
            text, buttons = await get_readable_message(
                sid, is_user, page_no, status, page_step
            )
            if text is None:
                del status_dict[sid]
                if obj := intervals["status"].get(sid):
                    obj.cancel()
                    del intervals["status"][sid]
                return
            if text != status_dict[sid]["message"].text:
                message = await self.edit(
                    text, buttons, status_dict[sid]["message"]
                )
                if isinstance(message, str):
                    if message.startswith("Telegram says: [40"):
                        del status_dict[sid]
                        if obj := intervals["status"].get(sid):
                            obj.cancel()
                            del intervals["status"][sid]
                    else:
                        LOGGER.error(
                            f"Status with id: {sid} haven't been updated. Error: {message}"
                        )
                    return
                status_dict[sid]["message"].text = text
                status_dict[sid]["time"] = time()

        # async def sendStatus(self, message, user_id=0):

    async def sendStatus(self, user_id=0):
        if intervals["stopAll"]:
            return
        sid = user_id or self.message.chat.id
        is_user = bool(user_id)
        async with task_dict_lock:
            if sid in status_dict:
                page_no = status_dict[sid]["page_no"]
                status = status_dict[sid]["status"]
                page_step = status_dict[sid]["page_step"]
                text, buttons = await get_readable_message(
                    sid, is_user, page_no, status, page_step
                )
                if text is None:
                    del status_dict[sid]
                    if obj := intervals["status"].get(sid):
                        obj.cancel()
                        del intervals["status"][sid]
                    return
                old_message = status_dict[sid]["message"]
                message = await self.reply(text, buttons, force=False)
                if isinstance(message, str):
                    LOGGER.error(
                        f"Status with id: {sid} haven't been sent. Error: {message}"
                    )
                    return
                await self.delete(old_message)
                message.text = text
                status_dict[sid].update({"message": message, "time": time()})
            else:
                text, buttons = await get_readable_message(sid, is_user)
                if text is None:
                    return
                message = await self.reply(text, buttons, force=False)
                if isinstance(message, str):
                    LOGGER.error(
                        f"Status with id: {sid} haven't been sent. Error: {message}"
                    )
                    return
                message.text = text
                status_dict[sid] = {
                    "message": message,
                    "time": time(),
                    "page_no": 1,
                    "page_step": 1,
                    "status": "All",
                    "is_user": is_user,
                }
            if not intervals["status"].get(sid) and not is_user:
                intervals["status"][sid] = SetInterval(
                    Config.STATUS_UPDATE_INTERVAL, self.updateStatus, sid
                )

    async def deleteStatus(self):
        async with task_dict_lock:
            for key, data in list(status_dict.items()):
                try:
                    await self.delete(data["message"])
                    del status_dict[key]
                except Exception as e:
                    LOGGER.error(e)


class MessageUtils(_Pyrogram):
    def __init__(self, message=None, client=TgClient.bot, bot_token=Config.BOT_TOKEN):
        self.client = client
        self.message = message
        self.bot_token = bot_token
        super().__init__()

        """
async def main():
    
    # 5MB < photo
    # 50MB < file
    chat_id = "-1001721246454"
    text = "<a href='https://google.com'><b>Choice: </b></a>"
    photo = "https://telegra.ph/file/fb34422afb0bf0ecb1b5d.jpg"
    video = "https://telegra.ph/file/2fa02c9d507a8299b576d.mp4"
    reply_markup = json.dumps(
        {"inline_keyboard":
            [
                [
                    {"text": "Yes", "callback_data": "x"},
                    {"text": "No", "callback_data": "x"}
                ]
            ]
         }
    )

    bot = TgApi(Config.BOT_TOKEN)
    a = await bot.sendMessage(chat_id, text, reply_markup=reply_markup)
    b = await bot.sendPhoto(chat_id, photo, caption="Here is a photo", reply_markup=reply_markup)
    c = await bot.sendVideo(chat_id, video, caption="Here is a video", reply_markup=reply_markup)
    print(a, b, c)
    


if __name__ == "__main__":
    run(main())
"""
