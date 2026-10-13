# -*- coding: utf-8 -*-
"""测试每个翻译接口在你的网络下能不能用。"""
import time
import engines

SAMPLES = [
    ("Hello world. This tool translates the text you select.", "en", "zh"),
    ("你好，这是一个划词翻译工具。", "zh", "en"),
]

print("正在测试各个翻译接口，请稍等……\n")
ok_list = []
for key, eng in engines.ENGINES.items():
    good = True
    for text, src, tgt in SAMPLES:
        t0 = time.time()
        try:
            res = engines.translate(key, text, src, tgt)
            print(f"[可用] {eng.name:<12} {src}→{tgt}  {time.time() - t0:.1f}秒  {res}")
        except Exception as e:
            good = False
            print(f"[失败] {eng.name:<12} {src}→{tgt}  {engines.friendly_error(e)}")
    if good:
        ok_list.append(eng.name)
    print()

print("在你的网络下可用的接口：", "、".join(ok_list) if ok_list else "无")
print("建议只在托盘菜单里勾选可用的接口。")
