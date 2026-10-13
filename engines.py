# -*- coding: utf-8 -*-
"""翻译接口集合：全部为免费、无需注册的接口。
免费非官方接口随时可能变动，失败时会在弹窗里显示原因，不影响其他接口。
"""
import re
import time
import uuid
import urllib.parse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 8

_session = requests.Session()
_session.headers.update({"User-Agent": UA})

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


# ---------------------------------------------------------------- 工具函数
def detect_lang(text):
    """粗略判断是中文还是其他（按英文处理）。"""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    return "zh" if len(_CJK.findall(text)) / len(letters) > 0.3 else "en"


def prepare(text, src):
    """清理从 PDF 等处复制来的断行。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if src == "en":
        text = re.sub(r"-\n(?=[a-z])", "", text)          # 连字符断词
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)       # 段内换行 -> 空格
    else:
        text = re.sub(r"(?<=[\u4e00-\u9fff])\n(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def friendly_error(e):
    if isinstance(e, requests.exceptions.Timeout):
        return "请求超时（这个接口在你的网络下可能不可用）"
    if isinstance(e, requests.exceptions.ConnectionError):
        return "连接失败（这个接口在你的网络下连不上）"
    if isinstance(e, requests.exceptions.HTTPError):
        code = e.response.status_code if e.response is not None else "?"
        return f"接口返回错误 HTTP {code}"
    if isinstance(e, (ValueError, KeyError, IndexError, TypeError)):
        return "接口返回的数据无法解析（接口可能已变更）"
    msg = str(e).strip()
    return "翻译失败：" + (msg[:80] if msg else type(e).__name__)


# ---------------------------------------------------------------- 各接口
_bing_token = {"value": None, "expires": 0.0}


_bing_web = {"host": None, "ig": None, "key": None, "token": None, "expires": 0.0}
_IG_RE = re.compile(r'IG:"([^"]+)"')
_TOK_RE = re.compile(r'params_AbusePreventionHelper\s*=\s*\[\s*(\d+)\s*,\s*"([^"]+)"')


def _bing_web_init(host):
    r = _session.get(f"https://{host}/translator", timeout=TIMEOUT)
    r.raise_for_status()
    ig, tok = _IG_RE.search(r.text), _TOK_RE.search(r.text)
    if not ig or not tok:
        raise ValueError("必应翻译页面解析失败")
    _bing_web.update(host=urllib.parse.urlparse(r.url).netloc or host, ig=ig.group(1),
                     key=tok.group(1), token=tok.group(2), expires=time.time() + 30 * 60)


def _bing_web_call(text, tgt, host):
    for attempt in range(2):
        if attempt or _bing_web["host"] is None or time.time() > _bing_web["expires"]:
            _bing_web_init(host)
        b = _bing_web
        r = _session.post(
            f"https://{b['host']}/ttranslatev3",
            params={"isVertical": "1", "IG": b["ig"], "IID": "translator.5028"},
            data={"fromLang": "auto-detect", "to": "zh-Hans" if tgt == "zh" else "en",
                  "text": text, "token": b["token"], "key": b["key"]},
            headers={"Referer": f"https://{b['host']}/translator"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()
        if isinstance(d, list):
            return d[0]["translations"][0]["text"]
        _bing_web["host"] = None  # 令牌失效，重新获取后再试一次
    raise RuntimeError("必应令牌失效")


def bing(text, src, tgt):
    """微软必应：依次尝试 cn.bing.com 网页接口、www.bing.com、Edge 内置翻译接口。"""
    last = None
    for f in (lambda: _bing_web_call(text, tgt, "cn.bing.com"),
              lambda: _bing_web_call(text, tgt, "www.bing.com"),
              lambda: _bing_edge(text, src, tgt)):
        try:
            return f()
        except Exception as e:
            last = e
            _bing_web["host"] = None
    raise last


def _bing_edge(text, src, tgt):
    def get_token(force=False):
        if force or not _bing_token["value"] or time.time() > _bing_token["expires"]:
            r = _session.get("https://edge.microsoft.com/translate/auth", timeout=TIMEOUT)
            r.raise_for_status()
            _bing_token["value"] = r.text.strip()
            _bing_token["expires"] = time.time() + 8 * 60
        return _bing_token["value"]

    def call(token):
        return _session.post(
            "https://api-edge.cognitive.microsofttranslator.com/translate",
            params={"to": "zh-Hans" if tgt == "zh" else "en", "api-version": "3.0"},
            headers={"Authorization": "Bearer " + token},
            json=[{"Text": text}],
            timeout=TIMEOUT,
        )

    r = call(get_token())
    if r.status_code == 401:
        r = call(get_token(force=True))
    r.raise_for_status()
    return r.json()[0]["translations"][0]["text"]


_tencent_key = "browser-chrome-124.0.0-Windows_10-%s-%d" % (uuid.uuid4(), int(time.time() * 1000))


def tencent(text, src, tgt):
    """腾讯交互翻译 TranSmart 网页接口，国内可直连。"""
    lines = [ln for ln in text.split("\n") if ln.strip()] or [text]
    body = {
        "header": {"fn": "auto_translation", "client_key": _tencent_key},
        "type": "plain",
        "model_category": "normal",
        "source": {"lang": src, "text_list": lines},
        "target": {"lang": tgt},
    }
    r = _session.post("https://transmart.qq.com/api/imt", json=body,
                      headers={"Referer": "https://transmart.qq.com/"}, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    res = d.get("auto_translation")
    if not res:
        raise RuntimeError(str(d.get("message") or d)[:80])
    return "\n".join(res) if isinstance(res, list) else str(res)


def google(text, src, tgt):
    """谷歌翻译免费接口。国内一般需要代理。"""
    r = _session.post(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "auto", "tl": "zh-CN" if tgt == "zh" else "en", "dt": "t"},
        data={"q": text},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


def mymemory(text, src, tgt):
    """MyMemory 公共接口：匿名每天约 5000 字符额度，单次最多 500 字符。"""
    code = {"zh": "zh-CN", "en": "en"}
    r = _session.get(
        "https://api.mymemory.translated.net/get",
        params={"q": text[:500], "langpair": f"{code[src]}|{code[tgt]}"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    d = r.json()
    if str(d.get("responseStatus")) != "200":
        raise RuntimeError(str(d.get("responseDetails"))[:80])
    return d["responseData"]["translatedText"]


_LINGVA_HOSTS = ["https://lingva.ml", "https://lingva.lunar.icu", "https://translate.plausibility.cloud"]


def lingva(text, src, tgt):
    """Lingva：谷歌翻译的开源镜像，公共实例时好时坏。"""
    last = None
    for host in _LINGVA_HOSTS:
        try:
            url = f"{host}/api/v1/auto/{'zh' if tgt == 'zh' else 'en'}/{urllib.parse.quote(text[:1500], safe='')}"
            r = _session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()["translation"]
        except Exception as e:  # 换下一个实例
            last = e
    raise last


class Engine:
    def __init__(self, name, func, default=True, stable=True):
        self.name, self.func, self.default, self.stable = name, func, default, stable


# 顺序即弹窗里的显示顺序
ENGINES = {
    "tencent": Engine("腾讯交互翻译", tencent),
    "lingva": Engine("Lingva 谷歌镜像", lingva),
    "bing": Engine("微软必应", bing),
    "mymemory": Engine("MyMemory", mymemory),
    "google": Engine("谷歌翻译", google, default=False, stable=False),
}


def translate(key, text, src, tgt):
    result = ENGINES[key].func(text, src, tgt)
    result = (result or "").strip()
    if not result:
        raise RuntimeError("返回了空结果")
    return result
