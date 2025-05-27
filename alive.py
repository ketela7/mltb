from time import sleep
from requests import get as rget
from os import environ
from logging import error as logerror, info as log_info




if BASE_URL := environ.get('BASE_URL'):
    while True:
        try:
            rget(BASE_URL).status_code
            sleep(600)
        except Exception as e:
            logerror(f"alive.py: {e}")
            sleep(2)
            continue