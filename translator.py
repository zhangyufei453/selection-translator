# -*- coding: utf-8 -*-
"""划词翻译：在任何软件里选中文字，鼠标旁弹出翻译（多个免费接口同时显示）。

用法：
  · 用鼠标拖选 / 双击选中文字  -> 自动弹出翻译（可在托盘菜单关闭）
  · 选中文字后按 Alt+Q          -> 手动翻译（任何时候都有效）
  · 点弹窗外任意位置或按 Esc     -> 关闭弹窗
"""
import sys
import os
import json
import time
import queue
import ctypes
import logging
import threading
import subprocess
import tkinter as tk

import pyperclip
from pynput import mouse, keyboard

import engines

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# 高分屏下让 tkinter 和鼠标坐标保持一致
if IS_WIN:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "translator.log")

_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.getLogger().addHandler(_handler)
logging.getLogger().setLevel(logging.INFO)

FONT = "Microsoft YaHei UI" if IS_WIN else ("PingFang SC" if IS_MAC else "Noto Sans CJK SC")
BG, FG, MUTED, ACCENT, BORDER, SEP, ERR = (
    "#ffffff", "#1f2328", "#6e7781", "#0969da", "#c8d1da", "#eaeef2", "#cf222e")

DEFAULT_CONFIG = {
    "auto_select": True,          # 拖选后自动翻译
    "double_click": True,         # 双击选词后自动翻译
    "hotkey": "<cmd>+<shift>+y" if IS_MAC else "<alt>+q",
    "direction": "auto",          # auto: 中文->英文，其他->中文；to_zh: 全部->中文
    "engines": [k for k, e in engines.ENGINES.items() if e.default],
    "max_chars": 3000,
    "popup_width": 440,
    "font_size": 11,
    # 在这些程序里不做"自动"翻译（终端里 Ctrl+C 会中断正在运行的程序）；快捷键仍可用
    "ignore_apps": ["windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
                    "conhost.exe", "openconsole.exe", "mintty.exe", "putty.exe", "code.exe"],
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        save_config(cfg)
    except Exception:
        logging.exception("读取配置失败，使用默认配置")
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.exception("保存配置失败")


def open_path(path):
    try:
        if IS_WIN:
            os.startfile(path)
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        logging.exception("打开文件失败")


# ---------------------------------------------------------------- Windows 辅助
if IS_WIN:
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                     wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _user32.MonitorFromPoint.restype = wintypes.HANDLE
    _user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]


def foreground_exe():
    """当前前台窗口所属程序名（小写），失败返回空串。"""
    if not IS_WIN:
        return ""
    try:
        hwnd = _user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = _kernel32.OpenProcess(0x1000, False, pid.value)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            _kernel32.CloseHandle(h)
    except Exception:
        pass
    return ""


def monitor_workarea(x, y, root):
    """鼠标所在显示器的可用区域（支持多显示器）。"""
    if IS_WIN:
        try:
            hmon = _user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(mi)
            if _user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                w = mi.rcWork
                return w.left, w.top, w.right, w.bottom
        except Exception:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def make_noactivate(win):
    """让弹窗不抢焦点、不出现在任务栏。"""
    try:
        hwnd = _user32.GetParent(win.winfo_id())
        style = _user32.GetWindowLongW(hwnd, -20)
        _user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)
    except Exception:
        logging.exception("设置弹窗样式失败")


def startup_vbs_path():
    return os.path.join(os.environ.get("APPDATA", ""),
                        r"Microsoft\Windows\Start Menu\Programs\Startup", "HuaciFanyi.vbs")


def autostart_enabled():
    return IS_WIN and os.path.exists(startup_vbs_path())


# ---------------------------------------------------------------- 弹窗
class Popup:
    def __init__(self, app):
        self.app = app
        self.s = s = app.scale
        self.visible = False
        self.rect = None
        self.rows = {}
        self.results = {}
        self.scrollable = False
        fs = int(app.cfg.get("font_size", 11))
        self.f_text = (FONT, fs)
        self.f_small = (FONT, max(8, fs - 2))
        self.f_title = (FONT, max(8, fs - 2), "bold")

        w = self.win = tk.Toplevel(app.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=BORDER)

        outer = tk.Frame(w, bg=BG)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        hdr = tk.Frame(outer, bg=BG)
        hdr.pack(fill="x", padx=int(12 * s), pady=(int(8 * s), int(4 * s)))
        self.lbl_dir = tk.Label(hdr, bg=BG, fg=MUTED, font=self.f_small)
        self.lbl_dir.pack(side="left")
        close = tk.Label(hdr, text="✕", bg=BG, fg=MUTED, font=self.f_small, cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.hide())

        self.canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True, padx=(int(12 * s), int(6 * s)), pady=(0, int(10 * s)))
        self.body = tk.Frame(self.canvas, bg=BG)
        self.body_win = self.canvas.create_window(0, 0, anchor="nw", window=self.body)

        w.bind("<MouseWheel>", self._on_wheel)
        w.bind("<Escape>", lambda e: self.hide())

        if IS_WIN:  # 先在屏幕外显示一次，设置"不抢焦点"样式后再隐藏
            w.geometry("1x1+-32000+-32000")
            w.deiconify()
            w.update()
            make_noactivate(w)
            w.withdraw()

    def contains(self, x, y):
        r = self.rect
        return bool(self.visible and r and r[0] <= x < r[2] and r[1] <= y < r[3])

    def show(self, text, tgt, keys, px, py):
        s = self.s
        self.width = int(int(self.app.cfg.get("popup_width", 440)) * s)
        self.inner_w = self.width - int(20 * s)
        wrap = self.inner_w - int(6 * s)

        for c in self.body.winfo_children():
            c.destroy()
        self.rows.clear()
        self.results.clear()
        self.canvas.yview_moveto(0)

        self.lbl_dir.config(text="中文 → 英文" if tgt == "en" else "原文 → 中文")
        preview = " ".join(text.split())
        if len(preview) > 160:
            preview = preview[:160] + "…"
        tk.Label(self.body, text=preview, bg=BG, fg=MUTED, font=self.f_small, wraplength=wrap,
                 justify="left", anchor="w").pack(fill="x", pady=(0, int(4 * s)))

        if not keys:
            tk.Label(self.body, text="没有启用任何翻译接口，请右键托盘图标 → 翻译接口 里勾选。",
                     bg=BG, fg=ERR, font=self.f_text, wraplength=wrap, justify="left",
                     anchor="w").pack(fill="x")

        for k in keys:
            tk.Frame(self.body, bg=SEP, height=1).pack(fill="x", pady=(int(6 * s), int(6 * s)))
            head = tk.Frame(self.body, bg=BG)
            head.pack(fill="x")
            tk.Label(head, text=engines.ENGINES[k].name, bg=BG, fg=ACCENT,
                     font=self.f_title).pack(side="left")
            cp = tk.Label(head, text="", bg=BG, fg=MUTED, font=self.f_small, cursor="hand2")
            cp.pack(side="right")
            cp.bind("<Button-1>", lambda e, k=k, cp=cp: self._copy(k, cp))
            res = tk.Label(self.body, text="翻译中…", bg=BG, fg=MUTED, font=self.f_text,
                           wraplength=wrap, justify="left", anchor="w")
            res.pack(fill="x", pady=(int(2 * s), 0))
            self.rows[k] = (res, cp)

        self.anchor = (px, py)
        self.work = monitor_workarea(px, py, self.app.root)
        self._layout()
        if not self.visible:
            self.win.deiconify()
            self.visible = True
        self.win.lift()

    def set_result(self, key, ok, text):
        row = self.rows.get(key)
        if not row:
            return
        res, cp = row
        res.config(text=text, fg=FG if ok else ERR)
        if ok:
            self.results[key] = text
            cp.config(text="复制")
        self._layout()

    def _layout(self):
        s = self.s
        self.canvas.itemconfigure(self.body_win, width=self.inner_w)
        self.body.update_idletasks()
        bh = self.body.winfo_reqheight()
        l, t, r, b = self.work
        maxh = max(int(160 * s), int((b - t) * 0.6))
        ch = max(1, min(bh, maxh - int(40 * s)))
        self.scrollable = bh > ch
        self.canvas.configure(width=self.inner_w, height=ch, scrollregion=(0, 0, self.inner_w, bh))
        self.win.update_idletasks()
        W, h = self.width, self.win.winfo_reqheight()
        px, py = self.anchor
        x, y = px + int(12 * s), py + int(18 * s)
        if x + W > r:
            x = r - W
        x = max(x, l)
        if y + h > b:
            y = py - h - int(12 * s)
        y = max(y, t)
        self.win.geometry(f"{W}x{h}+{x}+{y}")
        self.rect = (x, y, x + W, y + h)

    def _copy(self, key, label):
        text = self.results.get(key)
        if not text:
            return
        try:
            pyperclip.copy(text)
            label.config(text="已复制 ✓", fg=ACCENT)
            label.after(1500, lambda: label.winfo_exists() and label.config(text="复制", fg=MUTED))
        except Exception:
            logging.exception("复制失败")

    def _on_wheel(self, e):
        if not self.scrollable:
            return
        d = e.delta
        steps = -int(d / 120) if abs(d) >= 120 else (-1 if d > 0 else 1)
        self.canvas.yview_scroll(steps, "units")

    def hide(self):
        if self.visible:
            self.win.withdraw()
            self.visible = False
            self.rect = None


# ---------------------------------------------------------------- 主程序
_MOD_NAMES = ["alt", "alt_l", "alt_r", "alt_gr", "ctrl", "ctrl_l", "ctrl_r",
              "shift", "shift_l", "shift_r", "cmd", "cmd_l", "cmd_r"]
MOD_KEYS = {getattr(keyboard.Key, n) for n in _MOD_NAMES if hasattr(keyboard.Key, n)}
_SENTINEL = "\u2063__huaci_sel__\u2063"


class App:
    def __init__(self):
        self.cfg = load_config()
        self.q = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.popup = Popup(self)
        self.req_id = 0
        self.busy = threading.Lock()
        self.kb = keyboard.Controller()
        self.pressed_mods = set()
        self._down = None
        self._last_click = (0, 0, 0.0)
        self.tray = None

        self.hotkey = None
        hk = self.cfg.get("hotkey") or ""
        if hk:
            try:
                self.hotkey = keyboard.HotKey(keyboard.HotKey.parse(hk), lambda: self.trigger(0.0, True))
            except Exception:
                logging.warning("快捷键格式不正确：%s", hk)

        self.mouse_listener = mouse.Listener(on_click=self._on_click)
        self.mouse_listener.start()
        self.kb_listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self.kb_listener.start()
        self._setup_tray()
        self.root.after(30, self._poll)
        logging.info("启动完成")

    # ---- 输入监听（在监听线程里运行，不能直接操作界面）
    def _on_click(self, x, y, button, pressed):
        try:
            if pressed:
                self.q.put(("press", x, y))
            if button != mouse.Button.left:
                return
            if pressed:
                self._down = None if self.popup.contains(x, y) else (x, y)
                return
            down, self._down = self._down, None
            if down is None or not self.cfg.get("auto_select", True):
                return
            now = time.time()
            dragged = max(abs(x - down[0]), abs(y - down[1])) >= 8
            lx, ly, lt = self._last_click
            dbl = (self.cfg.get("double_click", True) and now - lt < 0.5
                   and abs(x - lx) <= 6 and abs(y - ly) <= 6)
            self._last_click = (0, 0, 0.0) if dbl else (x, y, now)
            if dragged or dbl:
                self.trigger(0.15, False)
        except Exception:
            logging.exception("鼠标事件处理失败")

    def _on_press(self, key):
        try:
            if key in MOD_KEYS:
                self.pressed_mods.add(key)
            elif key == keyboard.Key.esc:
                self.q.put(("hide",))
            if self.hotkey:
                self.hotkey.press(self.kb_listener.canonical(key))
        except Exception:
            logging.exception("按键处理失败")

    def _on_release(self, key):
        try:
            self.pressed_mods.discard(key)
            if self.hotkey:
                self.hotkey.release(self.kb_listener.canonical(key))
        except Exception:
            logging.exception("按键处理失败")

    # ---- 取选中文字
    def trigger(self, delay, from_hotkey):
        threading.Thread(target=self._grab, args=(delay, from_hotkey), daemon=True).start()

    def _grab(self, delay, from_hotkey):
        if not self.busy.acquire(blocking=False):
            return
        try:
            time.sleep(delay)
            if from_hotkey:  # 等用户松开 Alt 等修饰键，避免变成 Ctrl+Alt+C
                t0 = time.time()
                while self.pressed_mods and time.time() - t0 < 1.0:
                    time.sleep(0.02)
            else:
                exe = foreground_exe()
                if exe and exe in [a.lower() for a in self.cfg.get("ignore_apps", [])]:
                    return
            text = self._copy_selection()
        except Exception:
            logging.exception("获取选中文字失败")
            text = ""
        finally:
            self.busy.release()
        if text:
            self.q.put(("show", text[: int(self.cfg.get("max_chars", 3000))]))

    def _copy_selection(self):
        """模拟 Ctrl+C 读取选中文字，然后恢复原剪贴板。"""
        try:
            old = pyperclip.paste()
        except Exception:
            old = None
        try:
            pyperclip.copy(_SENTINEL)
        except Exception:
            return ""
        mod = keyboard.Key.cmd if IS_MAC else keyboard.Key.ctrl
        with self.kb.pressed(mod):
            self.kb.press("c")
            self.kb.release("c")
        text = ""
        deadline = time.time() + 0.6
        while time.time() < deadline:
            time.sleep(0.04)
            try:
                cur = pyperclip.paste()
            except Exception:
                continue
            if cur != _SENTINEL:
                text = cur or ""
                break
        try:
            pyperclip.copy(old if old is not None else "")
        except Exception:
            pass
        return text.strip()

    # ---- 主线程消息循环
    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "show":
                    self._show(msg[1])
                elif kind == "result":
                    _, rid, key, ok, res = msg
                    if rid == self.req_id and self.popup.visible:
                        self.popup.set_result(key, ok, res)
                elif kind == "press":
                    if self.popup.visible and not self.popup.contains(msg[1], msg[2]):
                        self.popup.hide()
                elif kind == "hide":
                    self.popup.hide()
                elif kind == "quit":
                    self._quit()
                    return
        except queue.Empty:
            pass
        except Exception:
            logging.exception("界面更新失败")
        self.root.after(30, self._poll)

    def _show(self, raw):
        self.req_id += 1
        rid = self.req_id
        src = engines.detect_lang(raw)
        text = engines.prepare(raw, src)
        tgt = "en" if (src == "zh" and self.cfg.get("direction", "auto") == "auto") else "zh"
        keys = [k for k in self.cfg.get("engines", []) if k in engines.ENGINES]
        px, py = self.root.winfo_pointerxy()
        self.popup.show(text, tgt, keys, px, py)
        for k in keys:
            threading.Thread(target=self._run_engine, args=(rid, k, text, src, tgt), daemon=True).start()

    def _run_engine(self, rid, key, text, src, tgt):
        try:
            res, ok = engines.translate(key, text, src, tgt), True
        except Exception as e:
            logging.info("接口 %s 失败：%r", key, e)
            res, ok = engines.friendly_error(e), False
        self.q.put(("result", rid, key, ok, res))

    # ---- 托盘菜单
    def _setup_tray(self):
        if IS_MAC:  # macOS 上托盘需要占用主线程，暂不提供
            return
        try:
            import pystray
            from pystray import MenuItem as Item, Menu
        except Exception:
            logging.exception("托盘不可用")
            return
        cfg = self.cfg

        def toggle(name):
            def f(icon, item):
                cfg[name] = not cfg.get(name, True)
                save_config(cfg)
            return f

        def is_on(name):
            return lambda item: bool(cfg.get(name, True))

        def set_dir(v):
            def f(icon, item):
                cfg["direction"] = v
                save_config(cfg)
            return f

        def dir_is(v):
            return lambda item: cfg.get("direction", "auto") == v

        order = list(engines.ENGINES)

        def toggle_engine(k):
            def f(icon, item):
                lst = cfg.setdefault("engines", [])
                if k in lst:
                    lst.remove(k)
                else:
                    lst.append(k)
                    lst.sort(key=lambda x: order.index(x) if x in order else 99)
                save_config(cfg)
            return f

        def engine_on(k):
            return lambda item: k in cfg.get("engines", [])

        eng_items = [Item(e.name + ("（可能不稳定）" if not e.stable else ""),
                          toggle_engine(k), checked=engine_on(k))
                     for k, e in engines.ENGINES.items()]
        hk = (cfg.get("hotkey") or "无").replace("<", "").replace(">", "").title()

        menu = Menu(
            Item("选中文字后自动翻译", toggle("auto_select"), checked=is_on("auto_select")),
            Item("双击选词也自动翻译", toggle("double_click"), checked=is_on("double_click")),
            Item(f"手动翻译快捷键：{hk}", None, enabled=False),
            Menu.SEPARATOR,
            Item("翻译方向", Menu(
                Item("自动（中文→英文，其他→中文）", set_dir("auto"), checked=dir_is("auto"), radio=True),
                Item("全部翻译成中文", set_dir("to_zh"), checked=dir_is("to_zh"), radio=True))),
            Item("翻译接口（可多选，同时显示）", Menu(*eng_items)),
            Menu.SEPARATOR,
            Item("开机自动启动", self._toggle_autostart, checked=lambda item: autostart_enabled()),
            Item("打开配置文件", lambda: open_path(CONFIG_PATH)),
            Item("退出", self._tray_quit),
        )
        self.tray = pystray.Icon("huaci_fanyi", make_icon_image(), "划词翻译（运行中）", menu)
        self.tray.run_detached()

    def _toggle_autostart(self, icon, item):
        if not IS_WIN:
            return
        p = startup_vbs_path()
        try:
            if os.path.exists(p):
                os.remove(p)
                return
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}"'
            else:
                exe = sys.executable
                w = os.path.join(os.path.dirname(exe), "pythonw.exe")
                cmd = f'"{w if os.path.exists(w) else exe}" "{os.path.abspath(__file__)}"'
            line = 'CreateObject("WScript.Shell").Run "%s", 0, False\r\n' % cmd.replace('"', '""')
            with open(p, "w", encoding="mbcs") as f:
                f.write(line)
        except Exception:
            logging.exception("设置开机启动失败")

    def _tray_quit(self, icon, item):
        self.q.put(("quit",))

    def _quit(self):
        for l in (self.mouse_listener, self.kb_listener):
            try:
                l.stop()
            except Exception:
                pass
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def make_icon_image():
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(9, 105, 218, 255))
    font, txt = None, "译"
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "PingFang.ttc"):
        try:
            font = ImageFont.truetype(name, 40)
            break
        except Exception:
            pass
    if font is None:
        font, txt = ImageFont.load_default(), "T"
    box = d.textbbox((0, 0), txt, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((64 - w) / 2 - box[0], (64 - h) / 2 - box[1]), txt, font=font, fill="white")
    return img


def main():
    if IS_WIN:  # 防止重复启动
        global _MUTEX
        _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\HuaciFanyi_SingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:
            return
    try:
        App().run()
    except Exception:
        logging.exception("程序异常退出")
        raise


if __name__ == "__main__":
    main()
