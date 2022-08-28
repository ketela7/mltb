from re import findall as re_findall
from threading import Thread, Event
from time import time
from math import ceil
from html import escape
from psutil import virtual_memory, cpu_percent, disk_usage, net_io_counters
from requests import head as rhead
from urllib.request import urlopen

from bot import download_dict, download_dict_lock, STATUS_LIMIT, botStartTime, DOWNLOAD_DIR, WEB_PINCODE, BASE_URL
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.button_build import ButtonMaker

MAGNET_REGEX = r"magnet:\?xt=urn:btih:[a-zA-Z0-9]*"

URL_REGEX = r"(?:(?:https?|ftp):\/\/)?[\w/\-?=%.]+\.[\w/\-?=%.]+"

COUNT = 0
PAGE_NO = 1


class MirrorStatus:
    STATUS_UPLOADING = "Upload"
    STATUS_DOWNLOADING = "Download"
    STATUS_CLONING = "Clone"
    STATUS_WAITING = "Queue"
    STATUS_PAUSED = "Pause"
    STATUS_ARCHIVING = "Archive"
    STATUS_EXTRACTING = "Extract"
    STATUS_SPLITTING = "Split"
    STATUS_CHECKING = "CheckUp"
    STATUS_SEEDING = "Seed"

SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']


class setInterval:
    def __init__(self, interval, action):
        self.interval = interval
        self.action = action
        self.stopEvent = Event()
        thread = Thread(target=self.__setInterval)
        thread.start()

    def __setInterval(self):
        nextTime = time() + self.interval
        while not self.stopEvent.wait(nextTime - time()):
            nextTime += self.interval
            self.action()

    def cancel(self):
        self.stopEvent.set()

def get_readable_file_size(size_in_bytes) -> str:
    if size_in_bytes is None:
        return '0B'
    index = 0
    while size_in_bytes >= 1024:
        size_in_bytes /= 1024
        index += 1
    try:
        return f'{round(size_in_bytes, 2)}{SIZE_UNITS[index]}'
    except IndexError:
        return 'File too large'

def getDownloadByGid(gid):
    with download_dict_lock:
        for dl in list(download_dict.values()):
            if dl.gid() == gid:
                return dl
    return None

def getAllDownload(req_status: str):
    with download_dict_lock:
        for dl in list(download_dict.values()):
            status = dl.status()
            if req_status in ['all', status]:
                return dl
    return None

def bt_selection_buttons(id_: str):
    if len(id_) > 20:
        gid = id_[:12]
    else:
        gid = id_

    pincode = ""
    for n in id_:
        if n.isdigit():
            pincode += str(n)
        if len(pincode) == 4:
            break

    buttons = ButtonMaker()
    if WEB_PINCODE:
        buttons.buildbutton("Select Files", f"{BASE_URL}/app/files/{id_}")
        buttons.sbutton("Pincode", f"btsel pin {gid} {pincode}")
    else:
        buttons.buildbutton("Select Files", f"{BASE_URL}/app/files/{id_}?pin_code={pincode}")
    buttons.sbutton("Done Selecting", f"btsel done {gid} {id_}")
    return buttons.build_menu(2)

def get_progress_bar_string(status):
    completed = status.processed_bytes() / 8
    total = status.size_raw() / 8
    p = 0 if total == 0 else round(completed * 100 / total)
    p = min(max(p, 0), 100)
    cFull = p // 8
    p_str = '▣' * cFull
    p_str += '□' * (12 - cFull)
    p_str = f"[{p_str}] {status.progress()}"
    return p_str


class progress_style:


    def bar(self, current, totaled):
        completed = current / 8
        total = totaled / 8
        p = 0 if total == 0 else round(completed * 100 / total)
        p = min(max(p, 0), 100)
        cFull = p // 8
        p_str = '▣' * cFull
        p_str += '□' * (12 - cFull)
        p_str = f"[{p_str}] {round(completed * 100 / total, 2)}%"
        return p_str


    def disk_info(self, info_type, total, used, free):
        text = f"\n{self.bar(used, total)}"
        text += f"\n{info_type} {get_readable_file_size(used)}  /  {get_readable_file_size(free)}  of  {get_readable_file_size(total)}"
        return text


    def ram(self):
        ram = virtual_memory()
        msg = self.disk_info("RAM", ram.total, ram.used, ram.available)
        return msg


    def disk(self):
        total, used, free, _ = disk_usage('/')
        msg = self.disk_info("DISK", total, used, free)
        return msg


    def system_info(self, dl_speed, up_speed):
        msg = f"⚡{get_readable_file_size(dl_speed)}/s  ⚡{get_readable_file_size(up_speed)}/s"
        msg += f"\nCPU  <i>{cpu_percent()}%</i> | UPTIME  <i>{get_readable_time(time() - botStartTime)}</i>"
        msg += self.ram()
        msg += self.disk()
        msg += f"\n🔻  {get_readable_file_size(net_io_counters().bytes_recv)}  🔺  {get_readable_file_size(net_io_counters().bytes_sent)}  "
        return msg





def get_readable_message():
    with download_dict_lock:
        msg = ""
        if STATUS_LIMIT is not None:
            tasks = len(download_dict)
            global pages
            pages = ceil(tasks/STATUS_LIMIT)
            if PAGE_NO > pages and pages != 0:
                globals()['COUNT'] -= STATUS_LIMIT
                globals()['PAGE_NO'] -= 1
        for index, download in enumerate(list(download_dict.values())[COUNT:], start=1):
            msg += f"<a href='{download.message.link}'>{download.status()}</a>: "
            msg += f"<code>{escape(str(download.name()))}</code>"
            if download.status() not in [MirrorStatus.STATUS_SPLITTING, MirrorStatus.STATUS_SEEDING]:
                msg += f"\n{get_progress_bar_string(download)}"
                msg += f"\nProcess: <i>{get_readable_file_size(download.processed_bytes())} of {download.size()}</i>"
                msg += f"\nEta: <i>{download.eta()}</i>"
                msg += f"\nSpeed: <i>{download.speed()}</i>"

                if hasattr(download, 'seeders_num'):
                    try:
                        msg += f"\nSeeders: {download.seeders_num()} | Leechers: {download.leechers_num()}"
                    except:
                        pass
            elif download.status() == MirrorStatus.STATUS_SEEDING:
                msg += f"\nSize: {download.size()}"
                msg += f"\nSpeed: {download.upload_speed()} | Uploaded: {download.uploaded_bytes()}"
                msg += f"\nRatio: {download.ratio()} | Time: {download.seeding_time()}"
            else:
                msg += f"\nSize: {download.size()}"
            msg += f"\n<code>/{BotCommands.CancelMirror} {download.gid()}</code>"
            msg += "\n\n"
            if STATUS_LIMIT is not None and index == STATUS_LIMIT:
                break
        if len(msg) == 0:
            return None, None
        dl_speed = 0
        up_speed = 0
        for download in list(download_dict.values()):
            if download.status() == MirrorStatus.STATUS_DOWNLOADING:
                spd = download.speed()
                if 'K' in spd:
                    dl_speed += float(spd.split('K')[0]) * 1024
                elif 'M' in spd:
                    dl_speed += float(spd.split('M')[0]) * 1048576
            elif download.status() == MirrorStatus.STATUS_UPLOADING:
                spd = download.speed()
                if 'KB/s' in spd:
                    up_speed += float(spd.split('K')[0]) * 1024
                elif 'MB/s' in spd:
                    up_speed += float(spd.split('M')[0]) * 1048576
            elif download.status() == MirrorStatus.STATUS_SEEDING:
                spd = download.upload_speed()
                if 'K' in spd:
                    up_speed += float(spd.split('K')[0]) * 1024
                elif 'M' in spd:
                    up_speed += float(spd.split('M')[0]) * 1048576
        bmsg = progress_style().system_info(dl_speed, up_speed)



        if STATUS_LIMIT is not None and tasks > STATUS_LIMIT:
            msg += f"Page: {PAGE_NO}/{pages} | Tasks: {tasks}\n"
            buttons = ButtonMaker()
            buttons.sbutton("Previous", "status pre")
            buttons.sbutton("Next", "status nex")
            button = buttons.build_menu(2)
            return f"<b>{msg}{bmsg}</b>", button
        return f"<b>{msg}{bmsg}</b>", ""


def turn(data):
    try:
        with download_dict_lock:
            global COUNT, PAGE_NO
            if data[1] == "nex":
                if PAGE_NO == pages:
                    COUNT = 0
                    PAGE_NO = 1
                else:
                    COUNT += STATUS_LIMIT
                    PAGE_NO += 1
            elif data[1] == "pre":
                if PAGE_NO == 1:
                    COUNT = STATUS_LIMIT * (pages - 1)
                    PAGE_NO = pages
                else:
                    COUNT -= STATUS_LIMIT
                    PAGE_NO -= 1
        return True
    except:
        return False

def get_readable_time(seconds: int) -> str:
    result = ''
    (days, remainder) = divmod(seconds, 86400)
    days = int(days)
    if days != 0:
        result += f'{days}d'
    (hours, remainder) = divmod(remainder, 3600)
    hours = int(hours)
    if hours != 0:
        result += f'{hours}h'
    (minutes, seconds) = divmod(remainder, 60)
    minutes = int(minutes)
    if minutes != 0:
        result += f'{minutes}m'
    seconds = int(seconds)
    result += f'{seconds}s'
    return result

def is_url(url: str):
    url = re_findall(URL_REGEX, url)
    return bool(url)

def is_gdrive_link(url: str):
    return "drive.google.com" in url

def is_mega_link(url: str):
    return "mega.nz" in url or "mega.co.nz" in url

def get_mega_link_type(url: str):
    if "folder" in url:
        return "folder"
    elif "file" in url:
        return "file"
    elif "/#F!" in url:
        return "folder"
    return "file"

def is_magnet(url: str):
    magnet = re_findall(MAGNET_REGEX, url)
    return bool(magnet)

def new_thread(fn):
    """To use as decorator to make a function call threaded.
    Needs import
    from threading import Thread"""

    def wrapper(*args, **kwargs):
        thread = Thread(target=fn, args=args, kwargs=kwargs)
        thread.start()
        return thread

    return wrapper

def get_content_type(link: str) -> str:
    try:
        res = rhead(link, allow_redirects=True, timeout=5, headers = {'user-agent': 'Wget/1.12'})
        content_type = res.headers.get('content-type')
    except:
        try:
            res = urlopen(link, timeout=5)
            info = res.info()
            content_type = info.get_content_type()
        except:
            content_type = None
    return content_type
