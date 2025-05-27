from asyncio import Lock, sleep
from datetime import datetime, timedelta
from functools import partial
from io import BytesIO
from re import I, compile, findall, search
from time import time

from apscheduler.triggers.interval import IntervalTrigger
from bs4 import BeautifulSoup as bs
from feedparser import parse as feed_parse
from pyrogram.filters import create
from pyrogram.handlers import MessageHandler

from .. import LOGGER, rss_dict, scheduler
from ..core.config_manager import Config
from ..helper.ext_utils.bot_utils import (
    arg_parser,
    async_request,
    get_size_bytes,
    new_task,
)
from ..helper.ext_utils.db_handler import database
from ..helper.ext_utils.exceptions import RssShutdownException
from ..helper.ext_utils.help_messages import RSS_HELP_MESSAGE
from ..helper.ext_utils.status_utils import get_readable_file_size
from ..helper.telegram_helper.button_build import ButtonMaker
from ..helper.telegram_helper.filters import CustomFilters
from ..helper.telegram_helper.message_utils import MessageUtils

rss_dict_lock = Lock()
handler_dict = {}
size_regex = compile(r"(\d+(\.\d+)?\s?(GB|MB|KB|GiB|MiB|KiB))", I)

headers = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.5938.153 Mobile Safari/537.36",
    #"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    #"Accept-Language": "en-US,en;q=0.5",
}


def is_ph_link(url):
    return url.startswith("https://www.pornhub.com")


def is_javtiful_link(url):
    return url.startswith("https://id.javtiful.com")


def is_nekopoi_link(url):
    return url.startswith("https://nekopoi.care")


def is_onejav_link(url):
    return url.startswith("https://onejav.com")


def is_yt_link(url):
    return "youtube" in url


async def fetch_html(url: str) -> str:
    if is_ph_link(url) and not url.endswith("/videos?&page=1"):
        url += "/videos?&page=1"

    return (await async_request(url)).text


async def ph_scraper(url: str, last_feed: bool = False) -> list:
    """Scrape judul dan link dari PornHub"""
    results, base_url, size = [], "https://www.pornhub.com", 0
    html = await fetch_html(url)
    # if not html: return results
    soup = bs(html, "lxml")

    if any(x in url for x in ["/model/", "/pornstar/"]):
        cards = soup.select_one("div.profileContentLeft").find_all(
            "div", class_="wrap")
    elif "/channels/" in url:
        cards = soup.select("div.widgetContainer")[0].find_all(
            "div", class_="wrap flexibleHeight"
        )
    elif "video?o=cm" in url:
        cards = soup.find_all("div", class_="wrap flexibleHeight")[4:]
    else:
        return results

    for card in cards:
        try:
            a_tag = card.find("a")
            title = a_tag.get("title", "").replace(",", "").strip()
            link = base_url + a_tag.get("href", "").strip()
            if title and link:
                results.append((title, link, size))
                if last_feed:
                    break
        except Exception as e:
            LOGGER.warning(f"Feed_URL: {url}. Failed to parsing elemen: {e}")

    return results


async def javtiful_scraper(uncensored: bool = False, last_feed: bool = False) -> list:
    """Scraper untuk id.javtiful.com"""
    base_url = "https://id.javtiful.com"
    target = "/uncensored" if uncensored else "/censored"
    results = []
    size = 0

    html = await fetch_html(base_url + target)
    # if not html: return results
    soup = bs(html, "lxml")
    for x in soup.find_all("div", class_="col pb-3"):
        trailer = x.find_all("span")[2].get("data-trailer", "")
        a = x.find(
            "a",
            class_="done video-link text-decoration-none text-black hover-secondary fw-semibold",
        )
        title = a.get("title", "").replace(",", "").strip()
        link = a.get("href", "").strip()
        results.append((title, f"{link}|{trailer}", size))
        if last_feed:
            break

    return results


async def nekopoi_scraper(category: str = "hentai", last_feed: bool = False) -> list:
    """Scrape link download dari halaman NekoPoi"""
    base_url = "https://nekopoi.care"
    results = []
    size = 0  # T0D0
    if category not in ["jav", "hentai"]:
        return results
    html = await fetch_html(base_url + "/category/" + category)
    # if not html: return results

    soup = bs(html, "lxml")
    for x in soup.select("div.result")[0].find_all("div", class_="top"):
        # img_url = x.img['src']
        title = x.h2.text
        link_eps = x.a["href"]
        eps_content = await fetch_html(link_eps)
        soup_eps = bs(eps_content, "lxml")
        link_stream = "\n".join(
            x.iframe["src"]
            for x in soup_eps.select("div.show-stream")[0].find_all(
                "div", class_="openstream"
            )
        )

        link_dl = "\n".join(
            "\n[{}]\n{}".format(
                findall(r"\[(.*?)]", l.find("div",
                        class_="name").string)[-1], l.p
            )
            for l in soup_eps.select("div.boxdownload")[0].find_all(
                "div", class_="liner"
            )
        )  # .replace('<p>', '').replace('</p>', '')

        caption = f"#nekopoi_{category}\n{title}\n{link_stream}{link_dl}"
        if len(caption) > 4000:
            caption = f"#nekopoi_{category}\n{title[:30]}\n{link_stream}\n\n<a href='{
                link_eps
            }'>See more</a>"
        results.append((title, caption, size))
        if last_feed:
            return results

    return results


async def onejav_scraper(last_feed: bool = False) -> list:
    """Scrape informasi video dari OneJav"""
    base_url = "https://onejav.com"
    results = []
    size = 0  # T0D0
    html = await fetch_html(base_url + "/new")
    # if not html: return results
    soup = bs(html, "lxml")
    for c in soup.find_all("div", class_="card mb-3"):
        title = c.h5.a.text.strip().replace("\n", "")
        link = base_url + c.find("a", class_="button is-primary is-fullwidth").get(
            "href"
        )
        results.append((title, link, size))
        if last_feed:
            return results

    return results


async def yt_scraper(url, last_feed=False):
    html = await fetch_html(url)
    match = search(r'youtube\.com/channel/([a-zA-Z0-9_-]+)', html)
    if match:
        channelId = match.group(1)
        return await feed_scraper(
            "https://www.youtube.com/feeds/videos.xml?channel_id=" + channelId,
            last_feed=last_feed
        )
    return []


async def feed_scraper(url: str, last_feed: bool = False) -> list:
    results = []
    html = await fetch_html(url)
    # if not html: return results
    rss_d = feed_parse(html)
    for rss_i in rss_d.entries:
        title = rss_i["title"]
        try:
            link = rss_i["links"][1]["href"]
        except IndexError:
            link = rss_i["link"]
        if rss_i.get("size"):
            size = int(rss_i["size"])
        elif rss_i.get("summary"):
            summary = rss_i["summary"]
            matches = size_regex.findall(summary)
            sizes = [match[0] for match in matches]
            size = get_size_bytes(sizes[0]) if sizes else 0
        else:
            size = 0
        results.append((title, link, size))
        if last_feed:
            break
    return results


async def rss_menu(event):
    user_id = event.from_user.id
    buttons = ButtonMaker()
    buttons.data_button("Subscribe", f"rss sub {user_id}")
    buttons.data_button("Subscriptions", f"rss list {user_id} 0")
    buttons.data_button("Get Items", f"rss get {user_id}")
    buttons.data_button("Edit", f"rss edit {user_id}")
    buttons.data_button("Pause", f"rss pause {user_id}")
    buttons.data_button("Resume", f"rss resume {user_id}")
    buttons.data_button("Unsubscribe", f"rss unsubscribe {user_id}")
    if await CustomFilters.sudo("", event):
        buttons.data_button("All Subscriptions", f"rss listall {user_id} 0")
        buttons.data_button("Pause All", f"rss allpause {user_id}")
        buttons.data_button("Resume All", f"rss allresume {user_id}")
        buttons.data_button("Unsubscribe All", f"rss allunsub {user_id}")
        buttons.data_button("Delete User", f"rss deluser {user_id}")
        if scheduler.running:
            buttons.data_button("Shutdown Rss", f"rss shutdown {user_id}")
        else:
            buttons.data_button("Start Rss", f"rss start {user_id}")
    buttons.data_button("Close", f"rss close {user_id}")
    button = buttons.build_menu(2)
    msg = f"Rss Menu | Users: {len(rss_dict)} | Running: {scheduler.running}"
    return msg, button


async def update_rss_menu(query):
    msg, button = await rss_menu(query)
    await MessageUtils(query.message).edit(msg, button)


@new_task
async def get_rss_menu(_, message):
    msg, button = await rss_menu(message)
    await MessageUtils(message).reply(msg, button)


@new_task
async def rss_sub(_, message, pre_event):
    mu = MessageUtils(message)
    user_id = message.from_user.id
    handler_dict[user_id] = False
    if username := message.from_user.username:
        tag = f"@{username}"
    else:
        tag = message.from_user.mention
    msg = ""
    items = message.text.split("\n")
    for index, item in enumerate(items, start=1):
        args = item.split()
        if len(args) < 2:
            await mu.reply(
                f"{item}. Wrong Input format. Read help message before adding new subcription!",
            )
            continue
        title = args[0].strip()
        if (user_feeds := rss_dict.get(user_id, False)) and title in user_feeds:
            await mu.reply(
                f"This title {title} already subscribed! Choose another title!"
            )
            continue
        feed_link = args[1].strip()
        if feed_link.startswith(("-inf", "-exf", "-c")):
            await mu.reply(
                f"Wrong input in line {index}! Add Title! Read the example!",
            )
            continue
        inf_lists = []
        exf_lists = []
        if len(args) > 2:
            arg_base = {"-c": None, "-inf": None, "-exf": None, "-stv": None}
            arg_parser(args[2:], arg_base)
            cmd = arg_base["-c"]
            inf = arg_base["-inf"]
            exf = arg_base["-exf"]
            stv = arg_base["-stv"]
            if stv is not None:
                stv = stv.lower() == "true"
            if inf is not None:
                filters_list = inf.split("|")
                for x in filters_list:
                    y = x.split(" or ")
                    inf_lists.append(y)
            if exf is not None:
                filters_list = exf.split("|")
                for x in filters_list:
                    y = x.split(" or ")
                    exf_lists.append(y)
        else:
            inf = None
            exf = None
            cmd = None
            stv = False
        try:
            is_media = False
            if is_ph_link(feed_link):
                results = await ph_scraper(feed_link, last_feed=True)
            elif is_javtiful_link(feed_link):
                is_media = True
                results = await javtiful_scraper(last_feed=True)
            elif is_nekopoi_link(feed_link):
                results = await nekopoi_scraper(last_feed=True)
            elif is_onejav_link(feed_link):
                results = await onejav_scraper(last_feed=True)
            elif is_yt_link(feed_link):
                results = await yt_scraper(feed_link, last_feed=True)
            else:
                results = await feed_scraper(feed_link, last_feed=True)
            last_title, last_feed, size = results[0]
            if is_media:
                last_feed = last_feed.split("|")[0]
            msg += f"\n{index}."
            msg += f"\n<b>TITLE:</b> <code>{title}</code>"
            msg += f"\n<b>LINK:</b> <code>{feed_link}</code>"
            if cmd:
                msg += f"\n<b>CMD:</b> <code>{cmd}</code>"
            if inf:
                msg += f"\n<b>INF:</b> <code>{inf}</code>"
            if exf:
                msg += f"\n<b>EXF:</b> <code>{exf}</code>"
            if stv:
                msg += f"\n<b>STV:</b> <code>{stv}</code>"
            if size:
                msg += f"\n<b>SIZE:</b> {get_readable_file_size(size)}"
            msg += f"\n<b>L. RECORD:</b> <code>{last_title}</code>"
            msg += f"\n<b>L. LINK:</b> <code>{last_feed}</code>"

            async with rss_dict_lock:
                if rss_dict.get(user_id, False):
                    rss_dict[user_id][title] = {
                        "link": feed_link,
                        "last_feed": last_feed,
                        "last_title": last_title,
                        "inf": inf_lists,
                        "exf": exf_lists,
                        "paused": False,
                        "command": cmd,
                        "sensitive": stv,
                        "tag": tag,
                    }
                else:
                    rss_dict[user_id] = {
                        title: {
                            "link": feed_link,
                            "last_feed": last_feed,
                            "last_title": last_title,
                            "inf": inf_lists,
                            "exf": exf_lists,
                            "paused": False,
                            "command": cmd,
                            "sensitive": stv,
                            "tag": tag,
                        }
                    }
            LOGGER.info(
                f"Rss Feed Added: id: {user_id} - title: {title} - link: {
                    feed_link
                } - c: {cmd} - inf: {inf} - exf: {exf} - stv: {stv}"
            )
        except (IndexError, AttributeError) as e:
            emsg = f"The link: {
                feed_link
            } doesn't seem to be a RSS feed or it's region-blocked!"
            await mu.reply(emsg + "\nError: " + str(e))
        except Exception as e:
            await mu.reply(str(e))
    if msg:
        await database.rss_update(user_id)

        if len(msg) > 4000:
            with BytesIO(str.encode(msg)) as out_file:
                out_file.name = "rss_sub.txt"
                await mu.reply(document=out_file)
        else:
            await mu.reply(msg)
        is_sudo = await CustomFilters.sudo("", message)
        if scheduler.state == 2:
            scheduler.resume()
        elif is_sudo and not scheduler.running:
            add_job()
            scheduler.start()
    await update_rss_menu(pre_event)


async def get_user_id(title):
    async with rss_dict_lock:
        return next(
            (
                (True, user_id)
                for user_id, feed in rss_dict.items()
                if feed["title"] == title
            ),
            (False, False),
        )


@new_task
async def rss_update(_, message, pre_event, state):
    mu = MessageUtils(message)
    user_id = message.from_user.id
    handler_dict[user_id] = False
    titles = message.text.split()
    is_sudo = await CustomFilters.sudo("", message)
    updated = []
    for title in titles:
        title = title.strip()
        if not (res := rss_dict[user_id].get(title, False)):
            if is_sudo:
                res, user_id = await get_user_id(title)
            if not res:
                user_id = message.from_user.id
                await mu.reply(f"{title} not found!")
                continue
        istate = rss_dict[user_id][title].get("paused", False)
        if istate and state == "pause" or not istate and state == "resume":
            await mu.reply(f"{title} already {state}d!")
            continue
        async with rss_dict_lock:
            updated.append(title)
            if state == "unsubscribe":
                del rss_dict[user_id][title]
            elif state == "pause":
                rss_dict[user_id][title]["paused"] = True
            elif state == "resume":
                rss_dict[user_id][title]["paused"] = False
        if state == "resume":
            if scheduler.state == 2:
                scheduler.resume()
            elif is_sudo and not scheduler.running:
                add_job()
                scheduler.start()
        if is_sudo and Config.DATABASE_URL and user_id != message.from_user.id:
            await database.rss_update(user_id)
        if not rss_dict[user_id]:
            async with rss_dict_lock:
                del rss_dict[user_id]
            await database.rss_delete(user_id)
            if not rss_dict:
                await database.trunc_table("rss")
    if updated:
        LOGGER.info(f"Rss link with Title(s): {updated} has been {state}d!")
        await mu.reply(
            f"Rss links with Title(s): <code>{updated}</code> has been {state}d!",
        )
        if rss_dict.get(user_id):
            await database.rss_update(user_id)
    await update_rss_menu(pre_event)


async def rss_list(query, start, all_users=False):
    user_id = query.from_user.id
    buttons = ButtonMaker()
    if all_users:
        list_feed = f"<b>All subscriptions | Page: {int(start / 5)} </b>"
        async with rss_dict_lock:
            keysCount = sum(len(v.keys()) for v in rss_dict.values())
            index = 0
            for titles in rss_dict.values():
                for index, (title, data) in enumerate(
                    list(titles.items())[start: 5 + start]
                ):
                    list_feed += f"\n\n<b>Title:</b> <code>{title}</code>\n"
                    list_feed += f"<b>Feed Url:</b> <code>{data['link']}</code>\n"
                    list_feed += f"<b>Command:</b> <code>{data['command']}</code>\n"
                    list_feed += f"<b>Inf:</b> <code>{data['inf']}</code>\n"
                    list_feed += f"<b>Exf:</b> <code>{data['exf']}</code>\n"
                    list_feed += f"<b>Sensitive:</b> <code>{
                        data.get('sensitive', False)
                    }</code>\n"
                    list_feed += f"<b>Paused:</b> <code>{data['paused']}</code>\n"
                    list_feed += f"<b>User:</b> {data['tag'].replace('@', '', 1)}"
                    index += 1
                    if index == 5:
                        break
    else:
        list_feed = f"<b>Your subscriptions | Page: {int(start / 5)} </b>"
        async with rss_dict_lock:
            keysCount = len(rss_dict.get(user_id, {}).keys())
            for title, data in list(rss_dict[user_id].items())[start: 5 + start]:
                list_feed += f"\n\n<b>Title:</b> <code>{
                    title
                }</code>\n<b>Feed Url: </b><code>{data['link']}</code>\n"
                list_feed += f"<b>Command:</b> <code>{data['command']}</code>\n"
                list_feed += f"<b>Inf:</b> <code>{data['inf']}</code>\n"
                list_feed += f"<b>Exf:</b> <code>{data['exf']}</code>\n"
                list_feed += (
                    f"<b>Sensitive:</b> <code>{data.get('sensitive', False)}</code>\n"
                )
                list_feed += f"<b>Paused:</b> <code>{data['paused']}</code>\n"
    buttons.data_button("Back", f"rss back {user_id}")
    buttons.data_button("Close", f"rss close {user_id}")
    if keysCount > 5:
        for x in range(0, keysCount, 5):
            buttons.data_button(
                f"{int(x / 5)}", f"rss list {user_id} {x}", position="footer"
            )
    button = buttons.build_menu(2)
    if query.message.text.html == list_feed:
        return
    await MessageUtils(query.message).edit(list_feed, button)


@new_task
async def rss_get(_, message, pre_event):
    mu = MessageUtils(message)
    user_id = message.from_user.id
    handler_dict[user_id] = False
    args = message.text.split()
    if len(args) < 2:
        await mu.reply(
            f"{args}. Wrong Input format. You should add number of the items you want to get. Read help message before adding new subcription!",
        )
        await update_rss_menu(pre_event)
        return
    try:
        title = args[0]
        count = int(args[1])
        data = rss_dict[user_id].get(title, False)
        if data and count > 0:
            try:
                msg = await mu.reply(
                    f"Getting the last <b>{count}</b> item(s) from {title}"
                )
                html = (await async_request(data["link"])).text
                rss_d = feed_parse(html)
                item_info = ""
                for item_num in range(count):
                    try:
                        link = rss_d.entries[item_num]["links"][1]["href"]
                    except IndexError:
                        link = rss_d.entries[item_num]["link"]
                    item_info += f"<b>Name: </b><code>{
                        rss_d.entries[item_num]['title']
                        .replace('>', '')
                        .replace('<', '')
                    }</code>\n"
                    item_info += f"<b>Link: </b><code>{link}</code>\n\n"
                item_info_ecd = item_info.encode()
                if len(item_info_ecd) > 4000:
                    with BytesIO(item_info_ecd) as out_file:
                        out_file.name = f"rssGet {title} items_no. {count}.txt"
                        await mu.reply(document=out_file)
                    await mu.delete(msg)
                else:
                    await mu.edit(item_info, message=msg)
            except IndexError as e:
                LOGGER.error(str(e))
                await mu.edit("Parse depth exceeded. Try again with a lower value.",
                              message=msg)
            except Exception as e:
                LOGGER.error(e)
                await mu.edit(str(e), message=msg)
        else:
            await mu.reply("Enter a valid title. Title not found!")
    except Exception as e:
        LOGGER.error(str(e))
        await mu.reply(f"Enter a valid value!. {e}")
    await update_rss_menu(pre_event)


@new_task
async def rss_edit(_, message, pre_event):
    mu = MessageUtils(message)
    user_id = message.from_user.id
    handler_dict[user_id] = False
    items = message.text.split("\n")
    updated = False
    for item in items:
        args = item.split()
        title = args[0].strip()
        if len(args) < 2:
            await mu.reply(
                f"{item}. Wrong Input format. Read help message before editing!",
            )
            continue
        elif not rss_dict[user_id].get(title, False):
            await mu.reply("Enter a valid title. Title not found!")
            continue
        updated = True
        inf_lists = []
        exf_lists = []
        arg_base = {"-c": None, "-inf": None, "-exf": None, "-stv": None}
        arg_parser(args[1:], arg_base)
        cmd = arg_base["-c"]
        inf = arg_base["-inf"]
        exf = arg_base["-exf"]
        stv = arg_base["-stv"]
        async with rss_dict_lock:
            if stv is not None:
                stv = stv.lower() == "true"
                rss_dict[user_id][title]["sensitive"] = stv
            if cmd is not None:
                if cmd.lower() == "none":
                    cmd = None
                rss_dict[user_id][title]["command"] = cmd
            if inf is not None:
                if inf.lower() != "none":
                    filters_list = inf.split("|")
                    for x in filters_list:
                        y = x.split(" or ")
                        inf_lists.append(y)
                rss_dict[user_id][title]["inf"] = inf_lists
            if exf is not None:
                if exf.lower() != "none":
                    filters_list = exf.split("|")
                    for x in filters_list:
                        y = x.split(" or ")
                        exf_lists.append(y)
                rss_dict[user_id][title]["exf"] = exf_lists
    if updated:
        await database.rss_update(user_id)
    await update_rss_menu(pre_event)


@new_task
async def rss_delete(_, message, pre_event):
    handler_dict[message.from_user.id] = False
    users = message.text.split()
    for user in users:
        user = int(user)
        async with rss_dict_lock:
            del rss_dict[user]
        await database.rss_delete(user)
    await update_rss_menu(pre_event)


async def event_handler(client, query, pfunc):
    user_id = query.from_user.id
    handler_dict[user_id] = True
    start_time = time()

    async def event_filter(_, __, event):
        user = event.from_user or event.sender_chat
        return bool(
            user.id == user_id and event.chat.id == query.message.chat.id and event.text
        )

    handler = client.add_handler(MessageHandler(
        pfunc, create(event_filter)), group=-1)
    while handler_dict[user_id]:
        await sleep(0.5)
        if time() - start_time > 60:
            handler_dict[user_id] = False
            await update_rss_menu(query)
    client.remove_handler(*handler)


@new_task
async def rss_listener(client, query):
    user_id = query.from_user.id
    message = query.message
    mu = MessageUtils(message)
    data = query.data.split()
    if int(data[2]) != user_id and not await CustomFilters.sudo("", query):
        await query.answer(
            text="You don't have permission to use these buttons!", show_alert=True
        )
    elif data[1] == "close":
        await query.answer()
        handler_dict[user_id] = False
        await mu.delete(reply_to=True)
    elif data[1] == "back":
        await query.answer()
        handler_dict[user_id] = False
        await update_rss_menu(query)
    elif data[1] == "sub":
        await query.answer()
        handler_dict[user_id] = False
        buttons = ButtonMaker()
        buttons.data_button("Back", f"rss back {user_id}")
        buttons.data_button("Close", f"rss close {user_id}")
        button = buttons.build_menu(2)
        await mu.edit(RSS_HELP_MESSAGE, button)
        pfunc = partial(rss_sub, pre_event=query)
        await event_handler(client, query, pfunc)
    elif data[1] == "list":
        handler_dict[user_id] = False
        if len(rss_dict.get(int(data[2]), {})) == 0:
            await query.answer(text="No subscriptions!", show_alert=True)
        else:
            await query.answer()
            start = int(data[3])
            await rss_list(query, start)
    elif data[1] == "get":
        handler_dict[user_id] = False
        if len(rss_dict.get(int(data[2]), {})) == 0:
            await query.answer(text="No subscriptions!", show_alert=True)
        else:
            await query.answer()
            buttons = ButtonMaker()
            buttons.data_button("Back", f"rss back {user_id}")
            buttons.data_button("Close", f"rss close {user_id}")
            button = buttons.build_menu(2)
            await mu.edit(
                "Send one title with value separated by space get last X items.\nTitle Value\nTimeout: 60 sec.",
                button,
            )
            pfunc = partial(rss_get, pre_event=query)
            await event_handler(client, query, pfunc)
    elif data[1] in ["unsubscribe", "pause", "resume"]:
        handler_dict[user_id] = False
        if len(rss_dict.get(int(data[2]), {})) == 0:
            await query.answer(text="No subscriptions!", show_alert=True)
        else:
            await query.answer()
            buttons = ButtonMaker()
            buttons.data_button("Back", f"rss back {user_id}")
            if data[1] == "pause":
                buttons.data_button("Pause AllMyFeeds",
                                    f"rss uallpause {user_id}")
            elif data[1] == "resume":
                buttons.data_button("Resume AllMyFeeds",
                                    f"rss uallresume {user_id}")
            elif data[1] == "unsubscribe":
                buttons.data_button("Unsub AllMyFeeds",
                                    f"rss uallunsub {user_id}")
            buttons.data_button("Close", f"rss close {user_id}")
            button = buttons.build_menu(2)
            await mu.edit(
                f"Send one or more rss titles separated by space to {
                    data[1]
                }.\nTimeout: 60 sec.",
                button,
            )
            pfunc = partial(rss_update, pre_event=query, state=data[1])
            await event_handler(client, query, pfunc)
    elif data[1] == "edit":
        handler_dict[user_id] = False
        if len(rss_dict.get(int(data[2]), {})) == 0:
            await query.answer(text="No subscriptions!", show_alert=True)
        else:
            await query.answer()
            buttons = ButtonMaker()
            buttons.data_button("Back", f"rss back {user_id}")
            buttons.data_button("Close", f"rss close {user_id}")
            button = buttons.build_menu(2)
            msg = """Send one or more rss titles with new filters or command separated by new line.
Examples:
Title1 -c mirror -up remote:path/subdir -exf none -inf 1080 or 720 -stv true
Title2 -c none -inf none -stv false
Title3 -c mirror -rcf xxx -up xxx -z pswd -stv false
Note: Only what you provide will be edited, the rest will be the same like example 2: exf will stay same as it is.
Timeout: 60 sec. Argument -c for command and arguments
            """
            await mu.edit(msg, button)
            pfunc = partial(rss_edit, pre_event=query)
            await event_handler(client, query, pfunc)
    elif data[1].startswith("uall"):
        handler_dict[user_id] = False
        if len(rss_dict.get(int(data[2]), {})) == 0:
            await query.answer(text="No subscriptions!", show_alert=True)
            return
        await query.answer()
        if data[1].endswith("unsub"):
            async with rss_dict_lock:
                del rss_dict[int(data[2])]
            await database.rss_delete(int(data[2]))
            await update_rss_menu(query)
        elif data[1].endswith("pause"):
            async with rss_dict_lock:
                for title in list(rss_dict[int(data[2])].keys()):
                    rss_dict[int(data[2])][title]["paused"] = True
            await database.rss_update(int(data[2]))
        elif data[1].endswith("resume"):
            async with rss_dict_lock:
                for title in list(rss_dict[int(data[2])].keys()):
                    rss_dict[int(data[2])][title]["paused"] = False
            if scheduler.state == 2:
                scheduler.resume()
            await database.rss_update(int(data[2]))
        await update_rss_menu(query)
    elif data[1].startswith("all"):
        if len(rss_dict) == 0:
            await query.answer(text="No subscriptions!", show_alert=True)
            return
        await query.answer()
        if data[1].endswith("unsub"):
            async with rss_dict_lock:
                rss_dict.clear()
            await database.trunc_table("rss")
            await update_rss_menu(query)
        elif data[1].endswith("pause"):
            async with rss_dict_lock:
                for user in list(rss_dict.keys()):
                    for title in list(rss_dict[user].keys()):
                        rss_dict[int(data[2])][title]["paused"] = True
            if scheduler.running:
                scheduler.pause()
            await database.rss_update_all()
        elif data[1].endswith("resume"):
            async with rss_dict_lock:
                for user in list(rss_dict.keys()):
                    for title in list(rss_dict[user].keys()):
                        rss_dict[int(data[2])][title]["paused"] = False
            if scheduler.state == 2:
                scheduler.resume()
            elif not scheduler.running:
                add_job()
                scheduler.start()
            await database.rss_update_all()
    elif data[1] == "deluser":
        if len(rss_dict) == 0:
            await query.answer(text="No subscriptions!", show_alert=True)
        else:
            await query.answer()
            buttons = ButtonMaker()
            buttons.data_button("Back", f"rss back {user_id}")
            buttons.data_button("Close", f"rss close {user_id}")
            button = buttons.build_menu(2)
            msg = "Send one or more user_id separated by space to delete their resources.\nTimeout: 60 sec."
            await mu.edit(msg, button)
            pfunc = partial(rss_delete, pre_event=query)
            await event_handler(client, query, pfunc)
    elif data[1] == "listall":
        if not rss_dict:
            await query.answer(text="No subscriptions!", show_alert=True)
        else:
            await query.answer()
            start = int(data[3])
            await rss_list(query, start, all_users=True)
    elif data[1] == "shutdown":
        if scheduler.running:
            await query.answer()
            scheduler.shutdown(wait=False)
            await sleep(0.5)
            await update_rss_menu(query)
        else:
            await query.answer(text="Already Stopped!", show_alert=True)
    elif data[1] == "start":
        if not scheduler.running:
            await query.answer()
            add_job()
            scheduler.start()
            await update_rss_menu(query)
        else:
            await query.answer(text="Already Running!", show_alert=True)


async def rss_monitor():
    chat = Config.RSS_CHAT
    if not chat:
        LOGGER.warning("RSS_CHAT not added! Shutting down rss scheduler...")
        scheduler.shutdown(wait=False)
        return
    if len(rss_dict) == 0:
        scheduler.pause()
        return
    all_paused = True
    rss_topic_id = rss_chat_id = None
    if isinstance(chat, int):
        rss_chat_id = chat
    elif "|" in chat:
        rss_chat_id, rss_topic_id = list(
            map(
                lambda x: int(x) if x.lstrip("-").isdigit() else x,
                chat.split("|", 1),
            )
        )
    elif chat.lstrip("-").isdigit():
        rss_chat_id = int(chat)
    for user, items in list(rss_dict.items()):
        for title, data in items.items():
            try:
                if data["paused"]:
                    continue

                is_media = False
                feed_link = data["link"]
                if is_ph_link(feed_link):
                    results = await ph_scraper(feed_link)
                elif is_javtiful_link(feed_link):
                    is_media = True
                    results = await javtiful_scraper()
                elif is_nekopoi_link(feed_link):
                    results = await nekopoi_scraper()
                elif is_onejav_link(feed_link):
                    results = await onejav_scraper()
                elif is_yt_link(feed_link):
                    results = await yt_scraper(feed_link)
                else:
                    results = await feed_scraper(feed_link)

                last_title, last_feed, _ = results[0]
                if is_media:
                    last_feed, _ = last_feed.split("|")
                all_paused = False

                if data["last_feed"] == last_feed or data["last_title"] == last_title:
                    continue
                feed_count = 0
                while True:
                    try:
                        await sleep(Config.SEND_MSG_DELAY)
                    except:
                        raise RssShutdownException("Rss Monitor Stopped!")
                    try:
                        item_title, item_feed, size = results[feed_count]
                        if (
                            data["last_feed"] == item_feed
                            or data["last_title"] == item_title
                        ):
                            break
                    except ValueError:
                        break
                    except IndexError:
                        LOGGER.warning(
                            f"Reached Max index no. {feed_count} for this feed: {
                                title
                            }. Maybe you need to use less RSS_DELAY to not miss some torrents"
                        )
                        break
                    parse = True
                    for flist in data["inf"]:
                        if (
                            data.get("sensitive", False)
                            and all(x.lower() not in item_title.lower() for x in flist)
                        ) or (
                            not data.get("sensitive", False)
                            and all(x not in item_title for x in flist)
                        ):
                            parse = False
                            feed_count += 1
                            break
                    if not parse:
                        continue
                    for flist in data["exf"]:
                        if (
                            data.get("sensitive", False)
                            and any(x.lower() in item_title.lower() for x in flist)
                        ) or (
                            not data.get("sensitive", False)
                            and any(x in item_title for x in flist)
                        ):
                            parse = False
                            feed_count += 1
                            break
                    if not parse:
                        continue

                    video_url = None
                    if is_media:
                        item_feed, video_url = item_feed.split("|")

                    if command := data["command"]:
                        if (
                            size
                            and Config.RSS_SIZE_LIMIT
                            and Config.RSS_SIZE_LIMIT < size
                        ):
                            feed_count += 1
                            continue
                        cmd = command.split(maxsplit=1)
                        cmd.insert(1, item_feed)
                        feed_msg = " ".join(cmd)
                        if not feed_msg.startswith("/"):
                            feed_msg = f"/{feed_msg}"
                    else:
                        feed_msg = f"<b>Name: </b><code>{
                            item_title.replace('>', '').replace('<', '')
                        }</code>"
                        feed_msg += f"\n\n<b>Link: </b><code>{item_feed}</code>"
                        if size:
                            feed_msg += f"\n<b>Size: </b>{get_readable_file_size(size)}"
                    feed_msg += (
                        f"\n<b>Tag: </b><code>{data['tag']}</code> <code>{user}</code>"
                    )
                    await MessageUtils().sendRss(chat=chat, caption=feed_msg, video=video_url)
                    feed_count += 1
                async with rss_dict_lock:
                    if user not in rss_dict or not rss_dict[user].get(title, False):
                        continue
                    rss_dict[user][title].update(
                        {"last_feed": last_feed, "last_title": last_title}
                    )
                await database.rss_update(user)
                LOGGER.info(f"Feed Name: {title}")
                LOGGER.info(f"Last item: {last_feed}")
            except RssShutdownException as ex:
                LOGGER.info(ex)
                break
            except Exception as e:
                LOGGER.error(
                    f"{e} - Feed Name: {title} - Feed Link: {data['link']}", exc_info=True)
                continue
    if all_paused:
        scheduler.pause()


def add_job():
    scheduler.add_job(
        rss_monitor,
        trigger=IntervalTrigger(seconds=Config.RSS_DELAY),
        id="0",
        name="RSS",
        misfire_grace_time=15,
        max_instances=1,
        next_run_time=datetime.now() + timedelta(seconds=20),
        replace_existing=True,
    )


add_job()
scheduler.start()
