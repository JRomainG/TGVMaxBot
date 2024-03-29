import requests
from typing import Any, List


class Requests:
    USER_AGENT_STRING = (
        "Mozilla/5.0 (Windows NT 6.1; Win64; x64) Gecko/20100101 Firefox/81.0"
    )

    @staticmethod
    def do_get(url: str, params: dict = {}) -> str:
        res = requests.get(
            url=url, params=params, headers={"User-Agent": Requests.USER_AGENT_STRING}
        )
        return res.text

    @staticmethod
    def do_post(url: str, data: dict) -> str:
        res = requests.post(
            url=url, json=data, headers={"User-Agent": Requests.USER_AGENT_STRING}
        )
        return res.text


def reshape(l: list, width: int) -> List[list]:
    out = []
    for i in range(0, len(l), width):
        out.append(l[i : i + width])
    return out


def getlist(config: dict, key: str, obj_type: Any = str) -> List:
    array_string = config[key].strip()
    if not array_string:
        return []
    return [obj_type(x) for x in array_string.split(" ")]
