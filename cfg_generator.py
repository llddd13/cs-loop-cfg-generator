#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
循环 alias CFG 生成器（Source 引擎 / CS2 / CS:GO）
==================================================
把一串文本做成“按一次键发一条、发完自动回到第一条”的循环脚本，
文本、指令、循环前缀、绑键方式都能自己定。

生成出来的东西长这样
--------------------
独立模式（一个模块自己循环）：

    alias "demo01" "say 你好; alias demo demo02";
    alias "demo02" "say 大家好; alias demo demo03";
    ...
    alias "demo12" "say 再见; alias demo demo01";     ← 最后一条绕回第一条

    alias demo "demo01"

    bind p demo

多模块 + 一个切换键（每个模块各自循环，共用一个键换模块）：

    alias "one01" "say 你好; alias one one02";
    ...
    alias "modesw_m1" "echo 进入 one; alias TextRider one; alias one one01; alias modesw modesw_m2";
    ...
    alias modesw "modesw_m1"        ← 切换键指向的开关
    alias TextRider "one"           ← 当前生效的模块
    alias one "one01"

    bind p TextRider                ← 发消息键：发“当前模块”的下一条
    bind RALT modesw                ← 唯一切换键（默认右 Alt）

四种指令写法（每个模块独立选）
------------------------------
  1) 全体麦         →  alias "one01" "say 你好; alias one one02";
  2) 队伍麦         →  alias "one01" "say_team 收到; alias one one02";
  3) 自定义指令     →  指令框里自己写，例如 say !drop 或 sm_say：
                        alias "one01" "say !drop 你好; alias one one02";
  4) 整行自定义     →  消息框每行都是完整指令，不加前缀，分号保留：
                        alias "raw1" "say a; echo b; alias raw raw2";

序号位数
--------
  条数不到 10  → 1 位（1、2、3）
  条数不到 100 → 2 位（01、02、10、33）
  条数 100 及以上 → 3 位（001、002、240）

命令行（不开界面）
------------------
  py cfg_generator.py                              打开图形界面
  py cfg_generator.py --demo                       生成示例 cfg 到 ./output 目录
  py cfg_generator.py --json 项目.json -o 出.cfg   按 JSON 项目文件生成

依赖：仅 Python 标准库（tkinter 随 Python 自带）。文件按 UTF-8（可选 BOM）+ CRLF 写出，
保证中文不乱码。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
APP_TITLE = "循环 alias CFG 生成器"
VERSION = "1.1"
DEFAULT_SWITCH_KEY = "RALT"          # 切换开关默认右 Alt
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TRAIL_DIGITS_RE = re.compile(r"(\d+)$")


def pad_width(count: int) -> int:
    """后缀位数：不到 10 条 → 1 位；不到 100 条 → 2 位；否则 3 位。"""
    if count < 10:
        return 1
    if count < 100:
        return 2
    return 3


def sanitize(text: str, escape: bool = True) -> str:
    """整理一条消息：去掉换行/首尾空白；可选地把会破坏 cfg 的字符换掉。

    · 英文分号 ; —— cfg 用 ; 分隔命令，留着会把消息截断
    · 英文引号 " —— 会把 alias 的值提前结束
    · 连续斜杠 // —— 在 cfg 里是注释，会让后面的 "alias 前缀 xxx" 失效（循环卡死）
    """
    t = str(text).replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()
    if escape:
        t = t.replace('"', "'").replace(";", "；").replace("//", "/ /")
    return t


def sanitize_cmd(text: str, escape: bool = True) -> str:
    """自定义/整行指令模式：这一行本身就是一条 cfg 指令，所以保留分号。

    分号在这里是“命令分隔符”，留着才能写 say a; echo b 这种多条命令；
    只有英文引号（会提前结束 alias 的值）和 // （会当注释吃掉后面的循环指针）必须换掉。
    """
    t = str(text).replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()
    if escape:
        t = t.replace('"', "'").replace("//", "/ /")
    return t


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
@dataclass
class Module:
    """一个大循环模块：一套独立循环（前缀 + 序号）。"""
    prefix: str = "ez"              # 循环前缀，小条目 = 前缀 + 数字
    channel: str = "say"            # say 全体麦 / say_team 队伍麦 / custom 自定义指令
    messages: list = field(default_factory=list)   # 一行一条：指令后面的内容
    prompt_mode: str = "none"       # 切换提示：say_team / echo / none
    prompt_text: str = ""           # 提示文本
    bind_key: str = ""              # 独立模式下给这个模块单独绑的键（可留空）
    zero_based: bool = False        # 后缀从 0 开始编号（默认从 1 开始）
    custom_cmd: str = "say"         # channel == "custom" 时自己写的指令（say 这部分）
    full_line: bool = False         # 整行自定义：消息框每行就是完整指令，不再加前缀

    @staticmethod
    def from_dict(d: dict) -> "Module":
        m = Module()
        for k in ("prefix", "channel", "prompt_mode", "prompt_text", "bind_key", "custom_cmd"):
            if k in d and d[k] is not None:
                setattr(m, k, str(d[k]))
        for k in ("zero_based", "full_line"):
            if k in d:
                setattr(m, k, bool(d[k]))
        if not m.custom_cmd.strip():
            m.custom_cmd = "say"
        msgs = d.get("messages") or []
        m.messages = [str(x) for x in msgs]
        if m.channel not in ("say", "say_team", "custom"):
            m.channel = "say"
        if m.prompt_mode not in ("say_team", "echo", "none"):
            m.prompt_mode = "none"
        return m


@dataclass
class Options:
    """全局设置。"""
    # 绑键
    do_bind: bool = True
    bind_key: str = "p"
    # 多模块、唯一切换开关
    use_switch: bool = True
    switch_key: str = DEFAULT_SWITCH_KEY
    switch_name: str = "modesw"
    active_name: str = "TextRider"
    # 输出与文本处理
    escape: bool = True             # 自动把 " 和 ; 换成安全字符
    quote_inside: bool = False      # 在 alias 体内加引号（引号嵌套写法）
    blank_every_10: bool = True     # 每 10 条空一行（排版用）
    bom: bool = False               # UTF-8 BOM
    crlf: bool = True               # CRLF 换行

    @staticmethod
    def from_dict(d: dict) -> "Options":
        o = Options()
        for k, v in (d or {}).items():
            if hasattr(o, k) and not isinstance(v, (dict, list)):
                cur = getattr(o, k)
                setattr(o, k, bool(v) if isinstance(cur, bool) else str(v))
        return o


@dataclass
class Project:
    options: Options = field(default_factory=Options)
    modules: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"version": VERSION, "options": asdict(self.options),
                "modules": [asdict(m) for m in self.modules]}

    @staticmethod
    def from_dict(d: dict) -> "Project":
        return Project(options=Options.from_dict(d.get("options") or {}),
                       modules=[Module.from_dict(x) for x in (d.get("modules") or [])])


# --------------------------------------------------------------------------- #
# 校验 + 生成
# --------------------------------------------------------------------------- #
def _clean_messages(mod: Module, escape: bool) -> list:
    """整行自定义模式下，每条消息本身就是完整指令（保留分号）。"""
    out = []
    for raw in mod.messages:
        s = sanitize_cmd(raw, escape) if mod.full_line else sanitize(raw, escape)
        if s:
            out.append(s)
    return out


def entry_command(mod: Module, text: str) -> str:
    """拼出一条条目里 `; alias 前缀 xxx` 之前的那段指令。

    · 整行自定义：text 本身就是完整指令，原样使用
    · 自定义指令：`<自己写的指令> <内容>`
    · 全体麦 / 队伍麦：`say <内容>` / `say_team <内容>`
    """
    if mod.full_line:
        return text
    if mod.channel == "custom":
        head = (mod.custom_cmd or "").strip()
    else:
        head = mod.channel
    return f"{head} {text}".strip() if head else text


def validate(modules: list, opts: Options):
    """返回 (errors, warnings)。"""
    errors, warnings = [], []
    modules = modules or []
    if not modules:
        errors.append("还没有模块：请先点「新增模块」，再填消息内容。")
        return errors, warnings

    use_switch = opts.use_switch and len(modules) >= 2
    if opts.use_switch and len(modules) == 1:
        warnings.append("只有 1 个模块，按独立模式生成（不需要切换开关）。")

    # 前缀合法性 / 重名
    seen = {}
    cleaned = []
    for i, m in enumerate(modules, 1):
        p = (m.prefix or "").strip()
        if not p:
            errors.append(f"第 {i} 个模块：循环前缀不能为空。")
        elif not NAME_RE.match(p):
            errors.append(f"第 {i} 个模块：前缀“{p}”不合法（只能字母/数字/下划线，且不能以数字开头）。")
        if p and TRAIL_DIGITS_RE.search(p):
            warnings.append(f"模块前缀“{p}”以数字结尾，用“从 cfg 导入”时可能无法自动还原该模块名。")
        if p:
            if p in seen:
                errors.append(f"循环前缀“{p}”重复（第 {seen[p]} 个和第 {i} 个模块）。")
            else:
                seen[p] = i
        msgs = _clean_messages(m, opts.escape)
        if not msgs:
            errors.append(f"模块“{p or i}”：至少要有一条消息内容。")
        cleaned.append(msgs)
        if m.channel == "custom" and not (m.custom_cmd or "").strip():
            warnings.append(f"模块“{p}”：自定义指令是空的，已按 say 处理（写 say / say_team / echo 等）。")
        if not opts.escape:
            if m.full_line:
                bad = [x for x in msgs if '"' in x or "//" in x]
                what = "英文引号或 //"
            else:
                bad = [x for x in msgs if '"' in x or ";" in x or "//" in x]
                what = "英文引号、分号或 //"
            if bad:
                warnings.append(f"模块“{p}”：有 {len(bad)} 条消息含{what}，"
                                f"且未做转义，可能导致 cfg 失效。")

    # 切换开关相关（开关最多唯一，名字不能和模块前缀重复）
    if use_switch:
        sw, act = (opts.switch_name or "").strip(), (opts.active_name or "").strip()
        if not NAME_RE.match(sw):
            errors.append(f"切换开关名“{sw}”不合法（只能字母/数字/下划线）。")
        if not NAME_RE.match(act):
            errors.append(f"活动指针名“{act}”不合法（只能字母/数字/下划线）。")
        if sw and sw in seen:
            errors.append(f"切换开关名“{sw}”与模块前缀重复，请换一个名字。")
        if act and act in seen:
            errors.append(f"活动指针名“{act}”与模块前缀重复，请换一个名字。")
        if sw and sw == act:
            errors.append("切换开关名与活动指针名不能相同。")
        if not (opts.switch_key or "").strip():
            warnings.append("没有填切换键，将不会生成切换用的 bind 行（需要自己 bind）。")
    else:
        for i, m in enumerate(modules, 1):
            key = (m.bind_key or "").strip()
            if not key and not (len(modules) == 1 and opts.do_bind and (opts.bind_key or "").strip()):
                warnings.append(f"模块“{m.prefix or i}”：没有设置绑键，生成的 cfg 里不会有 bind 行。")

    if opts.do_bind and not (opts.bind_key or "").strip() and (len(modules) == 1 or use_switch):
        warnings.append("勾选了“生成 bind 行”但消息键是空的，已跳过这条 bind。")

    # 别名重名冲突检查
    names = {}

    def claim(name, who):
        if not name:
            return
        if name in names:
            errors.append(f"别名“{name}”重复定义：{names[name]} 与 {who} 冲突"
                          f"（改一下循环前缀，或删掉/加上一条消息即可错开）。")
        else:
            names[name] = who

    for i, (m, msgs) in enumerate(zip(modules, cleaned), 1):
        p = (m.prefix or "").strip()
        if not p or not msgs:
            continue
        w = pad_width(len(msgs))
        start = 0 if m.zero_based else 1
        for k in range(len(msgs)):
            claim(f"{p}{start + k:0{w}d}", f"模块 {i}（{p}）的消息条目")
        claim(p, f"模块 {i}（{p}）的循环指针")
    if use_switch:
        claim((opts.switch_name or "").strip(), "切换开关指针")
        claim((opts.active_name or "").strip(), "活动指针")
        for k in range(1, len(modules) + 1):
            claim(f"{(opts.switch_name or '').strip()}_m{k}", "切换开关节点")

    return errors, warnings


def emit_chain(mod: Module, opts: Options, lines: list, pointer: str) -> str:
    """写出一个模块的循环条目，返回第一条的别名。pointer 是循环指针别名。"""
    msgs = _clean_messages(mod, opts.escape)
    n = len(msgs)
    w = pad_width(n)
    start = 0 if mod.zero_based else 1
    q = opts.quote_inside

    def name(i: int) -> str:
        return f"{mod.prefix}{i:0{w}d}"

    for k, text in enumerate(msgs):
        idx = start + k
        nxt = start + (k + 1) % n            # 循环：最后一条回到第一条
        target = f'"{name(nxt)}"' if q else name(nxt)
        cmd = entry_command(mod, text)
        lines.append(f'alias "{name(idx)}" "{cmd}; alias {pointer} {target}";')
        if opts.blank_every_10 and (k + 1) % 10 == 0 and (k + 1) < n:
            lines.append("")
    return name(start)


def ignored_prompts(modules: list, opts: Options) -> list:
    """独立模式下被忽略的切换提示：返回 [(前缀, 方式, 文本), ...]。

    独立模式（不启用模块切换，或只有 1 个模块）没有“切换”这个动作，
    所以这些提示不写进 cfg，只在软件里弹窗告诉使用者。
    """
    if opts.use_switch and len(modules) >= 2:
        return []
    out = []
    for i, m in enumerate(modules or [], 1):
        mode = (m.prompt_mode or "none").strip()
        text = sanitize(m.prompt_text, True) if m.prompt_text else ""
        if mode != "none" and text:
            out.append(((m.prefix or f"模块{i}").strip(), mode, text))
    return out


def build_cfg(modules: list, opts: Options) -> str:
    """生成 cfg 文本（\\n 换行，最后会按 opts 转换）。"""
    errors, _ = validate(modules, opts)
    if errors:
        raise ValueError("\n".join(errors))

    use_switch = opts.use_switch and len(modules) >= 2
    lines: list = []

    if not use_switch:
        # ---------- 独立模式：每个模块自己循环 ----------
        for i, mod in enumerate(modules):
            if i:
                lines.append("")
            first = emit_chain(mod, opts, lines, mod.prefix)
            lines.append("")
            lines.append(f'alias {mod.prefix} "{first}"')
            key = (mod.bind_key or "").strip()
            if not key and len(modules) == 1 and opts.do_bind:
                key = (opts.bind_key or "").strip()
            if key:                                     # 不绑键就不写 bind 行
                lines.append("")
                lines.append(f"bind {key} {mod.prefix}")
        return "\n".join(lines) + "\n"

    # ---------- 多模块 + 唯一切换开关 ----------
    firsts = {}
    for i, mod in enumerate(modules):
        if i:
            lines.append("")
        firsts[mod.prefix] = emit_chain(mod, opts, lines, mod.prefix)

    lines.append("")
    n = len(modules)
    for k, mod in enumerate(modules):
        parts = []
        ptext = sanitize(mod.prompt_text, True) if mod.prompt_text else ""
        if ptext and mod.prompt_mode == "say_team":      # 三种提示方式
            parts.append(f'say_team "{ptext}"' if opts.quote_inside else f"say_team {ptext}")
        elif ptext and mod.prompt_mode == "echo":
            parts.append(f'echo "{ptext}"' if opts.quote_inside else f"echo {ptext}")
        target = f'"{mod.prefix}"' if opts.quote_inside else mod.prefix
        first = f'"{firsts[mod.prefix]}"' if opts.quote_inside else firsts[mod.prefix]
        nxt = f'"{(opts.switch_name)}_m{(k + 1) % n + 1}"' if opts.quote_inside \
            else f"{opts.switch_name}_m{(k + 1) % n + 1}"
        parts.append(f"alias {opts.active_name} {target}")      # 当前生效的大模块
        parts.append(f"alias {mod.prefix} {first}")             # 切进来时重置到第一条
        parts.append(f"alias {opts.switch_name} {nxt}")         # 唯一的开关，循环切换
        lines.append(f'alias "{opts.switch_name}_m{k + 1}" "'
                     + "; ".join(parts) + '";')

    lines.append("")
    lines.append(f'alias {opts.switch_name} "{opts.switch_name}_m1"')
    lines.append(f'alias {opts.active_name} "{modules[0].prefix}"')
    for mod in modules:
        lines.append(f'alias {mod.prefix} "{firsts[mod.prefix]}"')

    binds = []
    if opts.do_bind and (opts.bind_key or "").strip():
        binds.append(f"bind {opts.bind_key.strip()} {opts.active_name}")   # 发消息键
    if (opts.switch_key or "").strip():
        binds.append(f"bind {opts.switch_key.strip()} {opts.switch_name}")  # 切换键
    if binds:
        lines.append("")
        lines.extend(binds)

    return "\n".join(lines) + "\n"


def write_cfg(text: str, path: str, opts: Options) -> str:
    data = text.replace("\n", "\r\n") if opts.crlf else text
    with open(path, "w", encoding="utf-8-sig" if opts.bom else "utf-8", newline="") as f:
        f.write(data)
    return os.path.abspath(path)


# --------------------------------------------------------------------------- #
# 从已有 cfg 反向导入
# --------------------------------------------------------------------------- #
_ALIAS_LINE_RE = re.compile(r'^\s*alias\s+"?([A-Za-z_][A-Za-z0-9_]*)"?\s+"(.*)"\s*;?\s*$')
_TAIL_RE = re.compile(r";\s*alias\s+", re.I)


def parse_cfg(text: str) -> list:
    """从已有的循环 cfg 中还原出模块。

    返回 [(prefix, [messages], channel, custom_cmd, full_line), ...]
    · 有 "say 文本; alias 前缀 下一条" 这种尾巴的才算循环条目
    · 指令不是 say / say_team 的，按“自定义指令”还原
    · 一条里写了多条命令（含分号）的，按“整行自定义”还原
    """
    groups, order = {}, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        m = _ALIAS_LINE_RE.match(line)
        if not m:
            continue
        name, body = m.group(1), m.group(2)
        dm = TRAIL_DIGITS_RE.search(name)
        if not dm:                       # 不是“前缀+数字”的条目，跳过
            continue
        parts = _TAIL_RE.split(body)     # 去掉最后的 "; alias 指针 下一条"
        if len(parts) < 2:               # 没有循环尾巴 → 不是循环条目
            continue
        head = " ".join(parts[:-1]).strip()
        if not head:
            continue
        if len(head) >= 2 and head.startswith('"') and head.endswith('"'):
            head = head[1:-1].strip()
        prefix = name[: dm.start()]
        # 第一条指令的第一个单词 = 指令本身（say / say_team / echo / sm_say …），其余 = 内容
        mm = re.match(r"^(\S+)(?:\s+(.*))?$", head, re.S)
        cmd = mm.group(1)
        content = (mm.group(2) or "").strip()
        if len(content) >= 2 and content.startswith('"') and content.endswith('"'):
            content = content[1:-1].strip()
        if ";" in head:                  # 一条里写了多条命令 → 整行自定义
            cmd, content, full = "", head, True
        else:
            full = False
        rec = {"idx": int(dm.group(1)), "cmd": cmd, "content": content,
               "head": head, "full": full}
        groups.setdefault(prefix, [])
        if prefix not in order:
            order.append(prefix)
        groups[prefix].append(rec)

    result = []
    for p in order:
        items = sorted(groups[p], key=lambda x: x["idx"])
        full = any(x["full"] for x in items)
        first = items[0]
        if full:
            # 整个模块按整行自定义还原：每一行都用完整指令原文
            result.append((p, [x["head"] for x in items], "say", "say", True))
        elif first["cmd"] in ("say", "say_team"):
            result.append((p, [x["content"] for x in items], first["cmd"], "say", False))
        else:
            result.append((p, [x["content"] for x in items], "custom", first["cmd"], False))
    return result


# --------------------------------------------------------------------------- #
# 图形界面
# --------------------------------------------------------------------------- #
def launch_gui(initial: Project | None = None) -> None:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    proj = initial or Project(options=Options(), modules=[])

    root = tk.Tk()
    root.title(f"{APP_TITLE}  v{VERSION}")
    root.geometry("1120x780")
    root.minsize(940, 640)

    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except Exception:
        pass

    state = {"current": 0, "loading": False, "last_sig": None,
             "out_touched": False, "out_last": ""}

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True)
    tab_gen = ttk.Frame(nb)
    tab_prev = ttk.Frame(nb)
    tab_help = ttk.Frame(nb)
    nb.add(tab_gen, text=" 生成器 ")
    nb.add(tab_prev, text=" 预览 ")
    nb.add(tab_help, text=" 使用说明 ")

    # ---------------- 变量 ----------------
    v_prefix = tk.StringVar()
    v_channel = tk.StringVar(value="say")
    v_prompt = tk.StringVar(value="none")
    v_prompt_text = tk.StringVar()
    v_custom_cmd = tk.StringVar(value="say")     # 自定义指令：say 这部分自己写
    v_full_line = tk.BooleanVar(value=False)     # 整行自定义：每行都是完整指令
    v_modbind = tk.StringVar()
    v_zero = tk.BooleanVar(value=False)
    v_count = tk.StringVar(value="")

    # 输出文件名默认 = 第一个模块的前缀（还没有模块时先叫 mycfg.cfg）
    _first_prefix = (proj.modules[0].prefix if proj.modules else "") or "mycfg"
    v_out = tk.StringVar(value=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            f"{_first_prefix}.cfg"))
    v_dobind = tk.BooleanVar(value=True)
    v_bindkey = tk.StringVar(value="p")
    v_switch = tk.BooleanVar(value=True)
    v_switchkey = tk.StringVar(value=DEFAULT_SWITCH_KEY)
    v_switchname = tk.StringVar(value="modesw")
    v_active = tk.StringVar(value="TextRider")
    v_escape = tk.BooleanVar(value=True)
    v_quote = tk.BooleanVar(value=False)
    v_blank = tk.BooleanVar(value=True)
    v_bom = tk.BooleanVar(value=False)
    v_crlf = tk.BooleanVar(value=True)

    # ---- 输出文件名：默认 = 第一个模块的前缀.cfg；手动改过就不再自动跟随 ----
    def sync_out_name(*_):
        """把输出文件名同步成“第一个模块的前缀.cfg”。"""
        if state["out_touched"] or not proj.modules:
            return
        cur = v_out.get().strip()
        d = os.path.dirname(cur) if cur else ""
        if not d:
            d = os.path.dirname(os.path.abspath(__file__))
        if state["current"] == 0:
            first = (v_prefix.get() or "").strip()
        else:
            first = (proj.modules[0].prefix or "").strip()
        newp = os.path.join(d, (first or "mycfg") + ".cfg")
        state["out_last"] = newp
        if newp != cur:
            v_out.set(newp)

    def mark_out_touched(*_):
        if v_out.get().strip() != state["out_last"]:
            state["out_touched"] = True

    def reset_out_name():
        state["out_touched"] = False
        sync_out_name()

    state["out_last"] = v_out.get().strip()
    v_out.trace_add("write", mark_out_touched)

    # ================= 生成器页 =================
    pw = ttk.PanedWindow(tab_gen, orient="horizontal")
    pw.pack(fill="both", expand=True, padx=8, pady=8)

    # ---- 左：模块列表 ----
    left = ttk.LabelFrame(pw, text="大循环模块（循环前缀）")
    lw = ttk.Frame(left)
    lw.pack(fill="both", expand=True, padx=6, pady=6)
    lb = tk.Listbox(lw, width=26, exportselection=False, activestyle="dotbox")
    lsb = ttk.Scrollbar(lw, command=lb.yview)
    lb.configure(yscrollcommand=lsb.set)
    lb.pack(side="left", fill="both", expand=True)
    lsb.pack(side="right", fill="y")
    lbar = ttk.Frame(left)
    lbar.pack(fill="x", padx=6, pady=(0, 6))
    v_list_hint = tk.StringVar(value="")
    ttk.Label(left, textvariable=v_list_hint, foreground="#666").pack(
        anchor="w", padx=8, pady=(0, 6))

    # ---- 右：当前模块设置 ----
    right = ttk.LabelFrame(pw, text="当前模块设置")
    right.columnconfigure(1, weight=1)
    right.rowconfigure(8, weight=1)

    ttk.Label(right, text="循环前缀：").grid(row=0, column=0, sticky="w", padx=6, pady=3)
    e_prefix = ttk.Entry(right, textvariable=v_prefix, width=20)
    e_prefix.grid(row=0, column=1, sticky="w", pady=3)

    ttk.Label(right, text="发送频道：").grid(row=1, column=0, sticky="w", padx=6, pady=3)
    f_ch = ttk.Frame(right)
    f_ch.grid(row=1, column=1, columnspan=3, sticky="ew", pady=3)
    ttk.Radiobutton(f_ch, text="全体麦  say", variable=v_channel, value="say").pack(side="left")
    ttk.Radiobutton(f_ch, text="队伍麦  say_team", variable=v_channel,
                    value="say_team").pack(side="left", padx=12)
    ttk.Radiobutton(f_ch, text="自定义指令", variable=v_channel,
                    value="custom").pack(side="left", padx=(12, 2))
    ttk.Entry(f_ch, textvariable=v_custom_cmd, width=16).pack(side="left")
    ttk.Label(f_ch, text="例如 say / say_team / echo", foreground="#666").pack(side="left", padx=4)

    f_line = ttk.Frame(right)
    f_line.grid(row=2, column=1, columnspan=3, sticky="w", pady=1)
    ttk.Checkbutton(f_line, text="整行自定义（消息框里每行写完整指令，不再自动加前缀，"
                                 "分号会保留）", variable=v_full_line).pack(side="left")

    ttk.Label(right, text="切换提示（仅模块切换时写入）：").grid(row=3, column=0, sticky="w",
                                                                 padx=6, pady=3)
    f_pr = ttk.Frame(right)
    f_pr.grid(row=3, column=1, columnspan=3, sticky="w", pady=3)
    ttk.Radiobutton(f_pr, text="控制台  echo", variable=v_prompt, value="echo").pack(side="left")
    ttk.Radiobutton(f_pr, text="队伍麦  say_team", variable=v_prompt,
                    value="say_team").pack(side="left", padx=14)
    ttk.Radiobutton(f_pr, text="不提示", variable=v_prompt, value="none").pack(side="left")

    ttk.Label(right, text="提示文本：").grid(row=4, column=0, sticky="w", padx=6, pady=3)
    ttk.Entry(right, textvariable=v_prompt_text).grid(row=4, column=1, sticky="ew", pady=3,
                                                      padx=(0, 6))

    ttk.Label(right, text="单独绑键：").grid(row=5, column=0, sticky="w", padx=6, pady=3)
    f_mb = ttk.Frame(right)
    f_mb.grid(row=5, column=1, columnspan=3, sticky="w", pady=3)
    ttk.Entry(f_mb, textvariable=v_modbind, width=10).pack(side="left")
    ttk.Label(f_mb, text="（仅“未启用模块切换”时使用；留空则该模块不生成 bind）"
              ).pack(side="left", padx=6)
    ttk.Checkbutton(f_mb, text="后缀从 0 开始", variable=v_zero).pack(side="left", padx=10)

    ttk.Label(right, text="消息内容：").grid(row=6, column=0, sticky="w", padx=6, pady=(8, 0))
    f_tools = ttk.Frame(right)
    f_tools.grid(row=6, column=1, columnspan=3, sticky="ew", pady=(8, 0), padx=(0, 6))

    v_msg_hint = tk.StringVar(value="")
    ttk.Label(right, textvariable=v_msg_hint).grid(row=7, column=0, columnspan=4, sticky="w",
                                                   padx=6, pady=(6, 0))
    f_txt = ttk.Frame(right)
    f_txt.grid(row=8, column=0, columnspan=4, sticky="nsew", padx=6, pady=(2, 6))
    txt = tk.Text(f_txt, height=16, wrap="word", undo=True)
    tsb = ttk.Scrollbar(f_txt, command=txt.yview)
    txt.configure(yscrollcommand=tsb.set)
    txt.pack(side="left", fill="both", expand=True)
    tsb.pack(side="right", fill="y")

    ttk.Label(f_tools, textvariable=v_count, foreground="#0a5").pack(side="right", padx=6)

    def update_msg_hint(*_):
        """消息框上方那行说明随模式变化。"""
        if v_full_line.get():
            v_msg_hint.set("一行一条，每行都写完整指令（空行会被忽略）。"
                           "例：say 你好   /   say_team 收到   /   say a; echo b")
        elif v_channel.get() == "custom":
            head = v_custom_cmd.get().strip() or "say"
            v_msg_hint.set(f"一行一条（空行会被忽略），就是 [{head}] 后面的内容：")
        else:
            v_msg_hint.set(f"一行一条（空行会被忽略），就是 [{v_channel.get()}] 后面的内容：")

    # ---- 提交 / 载入 ----
    def commit():
        if state["loading"]:
            return
        i = state["current"]
        if i is None or i < 0 or i >= len(proj.modules):
            return
        m = proj.modules[i]
        m.prefix = v_prefix.get().strip()
        m.channel = v_channel.get()
        m.prompt_mode = v_prompt.get()
        m.prompt_text = v_prompt_text.get().strip()
        m.bind_key = v_modbind.get().strip()
        m.zero_based = bool(v_zero.get())
        m.custom_cmd = v_custom_cmd.get().strip() or "say"
        m.full_line = bool(v_full_line.get())
        raw = txt.get("1.0", "end")
        msgs = [ln.strip() for ln in raw.split("\n")]
        while msgs and not msgs[-1]:
            msgs.pop()
        m.messages = msgs

    def _used_alias_names():
        """当前工程里已经占用的别名（模块指针、消息条目、开关节点等）。"""
        used = set()
        for m in proj.modules:
            p = (m.prefix or "").strip()
            if not p:
                continue
            used.add(p)
            msgs = [x for x in m.messages if str(x).strip()]
            if msgs:
                w = pad_width(len(msgs))
                st = 0 if m.zero_based else 1
                for k in range(len(msgs)):
                    used.add(f"{p}{st + k:0{w}d}")
        sw = (v_switchname.get() or "").strip()
        if sw:
            used.add(sw)
            for k in range(1, 21):
                used.add(f"{sw}_m{k}")
        act = (v_active.get() or "").strip()
        if act:
            used.add(act)
        return used

    def _free_prefix(base):
        """(base 已被占用时) 选一个不会撞名的新前缀——用字母后缀，避免以数字结尾。"""
        used = _used_alias_names()
        if base and base not in used:
            return base
        for ch in "abcdefghijklmnopqrstuvwxyz":
            cand = f"{base}{ch}"
            if cand not in used:
                return cand
        i = 2
        while f"{base}_x{i}" in used:
            i += 1
        return f"{base}_x{i}"

    def refresh_list(select=None):
        cur = state["current"] if select is None else select
        lb.delete(0, "end")
        for i, m in enumerate(proj.modules, 1):
            n = len([x for x in m.messages if str(x).strip()])
            label = f"{i:>2}. {m.prefix or '(空)'}    {n} 条"
            if m.prompt_mode == "echo":
                label += "  [echo]"
            elif m.prompt_mode == "say_team":
                label += "  [team]"
            if m.channel == "say_team":
                label += "  [队伍麦]"
            elif m.channel == "custom":
                label += f"  [{m.custom_cmd or 'say'}]"
            if m.full_line:
                label += "  [整行]"
            lb.insert("end", label)
        if proj.modules:
            state["current"] = max(0, min(cur, len(proj.modules) - 1))
            lb.selection_clear(0, "end")
            lb.selection_set(state["current"])
            lb.activate(state["current"])
            v_list_hint.set(f"共 {len(proj.modules)} 个模块")
        else:
            v_list_hint.set("还没有模块，点下面「新增模块」开始")
        update_count()

    def load_module(i):
        if not proj.modules:
            return
        i = max(0, min(i, len(proj.modules) - 1))
        state["loading"] = True
        state["current"] = i
        m = proj.modules[i]
        v_prefix.set(m.prefix)
        v_channel.set(m.channel if m.channel in ("say", "say_team", "custom") else "say")
        v_prompt.set(m.prompt_mode if m.prompt_mode in ("say_team", "echo", "none") else "none")
        v_prompt_text.set(m.prompt_text)
        v_modbind.set(m.bind_key)
        v_zero.set(bool(m.zero_based))
        v_custom_cmd.set(m.custom_cmd or "say")
        v_full_line.set(bool(m.full_line))
        txt.delete("1.0", "end")
        txt.insert("1.0", "\n".join(m.messages))
        state["loading"] = False
        update_count()
        update_msg_hint()

    def update_count(*_):
        """实时显示条数与后缀位数。"""
        if state["loading"]:
            return
        sync_out_name()
        raw = txt.get("1.0", "end")
        msgs = [x for x in (ln.strip() for ln in raw.split("\n")) if x]
        n = len(msgs)
        if n == 0:
            v_count.set("0 条")
            return
        start = 0 if v_zero.get() else 1
        w = pad_width(n)
        first = f"{v_prefix.get().strip() or 'xx'}{start:0{w}d}"
        last = f"{v_prefix.get().strip() or 'xx'}{start + n - 1:0{w}d}"
        v_count.set(f"{n} 条 · 后缀 {w} 位 · {first} → {last}")

    def on_select(_evt=None):
        sel = lb.curselection()
        if not sel:
            return
        new = sel[0]
        if new == state["current"]:
            return
        commit()
        load_module(new)
        refresh_list(new)

    lb.bind("<<ListboxSelect>>", on_select)
    for var in (v_prefix, v_channel, v_prompt, v_prompt_text, v_modbind, v_zero):
        var.trace_add("write", lambda *_: update_count())
    for var in (v_channel, v_custom_cmd, v_full_line):
        var.trace_add("write", lambda *_: update_msg_hint())

    def add_module():
        commit()
        used = _used_alias_names()
        # 自动起名：ma、mb、mc…（不用数字结尾，避免“从 cfg 导入”认不出前缀）
        base = None
        for i in range(26):
            cand = f"m{chr(ord('a') + i)}"
            if cand not in used:
                base = cand
                break
        prefix = _free_prefix(base or "m")
        proj.modules.append(Module(prefix=prefix, messages=["第一条示例", "第二条示例", "第三条示例"],
                                   prompt_mode="echo", prompt_text=f"进入 {prefix}"))
        refresh_list(len(proj.modules) - 1)
        load_module(len(proj.modules) - 1)

    def dup_module():
        commit()
        if not proj.modules:
            messagebox.showinfo("提示", "还没有模块，先点「新增模块」。")
            return
        m = proj.modules[state["current"]]
        new = Module(**asdict(m))
        new.prefix = _free_prefix(m.prefix + "_copy")
        proj.modules.insert(state["current"] + 1, new)
        refresh_list(state["current"] + 1)
        load_module(state["current"] + 1)

    def del_module():
        commit()
        if not proj.modules:
            messagebox.showinfo("提示", "还没有模块，先点「新增模块」。")
            return
        i = state["current"]
        if not messagebox.askyesno("确认", f"删除模块“{proj.modules[i].prefix}”？"):
            return
        proj.modules.pop(i)
        if proj.modules:
            refresh_list(min(i, len(proj.modules) - 1))
            load_module(state["current"])
        else:                            # 删到一个不剩：清空编辑区
            refresh_list(0)
            state["current"] = 0
            state["loading"] = True
            v_prefix.set("")
            v_prompt_text.set("")
            v_modbind.set("")
            txt.delete("1.0", "end")
            state["loading"] = False
            update_count()

    def move(delta):
        commit()
        i = state["current"]
        j = i + delta
        if j < 0 or j >= len(proj.modules):
            return
        proj.modules[i], proj.modules[j] = proj.modules[j], proj.modules[i]
        refresh_list(j)
        load_module(j)

    for text, cmd in (("新增模块", add_module), ("复制模块", dup_module),
                      ("删除模块", del_module), ("上移 ▲", lambda: move(-1)),
                      ("下移 ▼", lambda: move(1))):
        ttk.Button(lbar, text=text, command=cmd, width=11).pack(side="left", padx=2, pady=2)

    # ---- 消息框工具条 ----
    def import_txt():
        p = filedialog.askopenfilename(title="选择文本文件（一行一条消息）",
                                       filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not p:
            return
        try:
            with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
                lines = [ln.strip() for ln in f.read().splitlines()]
        except Exception as e:
            messagebox.showerror("读取失败", str(e))
            return
        lines = [x for x in lines if x]
        if not lines:
            messagebox.showinfo("提示", "文件里没有有效内容。")
            return
        if messagebox.askyesno("导入方式", f"共 {len(lines)} 条。\n\n是=追加到当前模块，否=替换当前模块内容"):
            cur = txt.get("1.0", "end").rstrip("\n")
            txt.insert("end", ("\n" if cur else "") + "\n".join(lines))
        else:
            txt.delete("1.0", "end")
            txt.insert("1.0", "\n".join(lines))
        update_count()

    def import_cfg():
        p = filedialog.askopenfilename(title="选择要反向导入的 cfg",
                                       filetypes=[("CFG 脚本", "*.cfg"), ("所有文件", "*.*")])
        if not p:
            return
        try:
            with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
                found = parse_cfg(f.read())
        except Exception as e:
            messagebox.showerror("读取失败", str(e))
            return
        if not found:
            messagebox.showinfo("提示", "没有识别到“前缀+数字”的循环条目。")
            return
        commit()
        proj.modules = [Module(prefix=p_, messages=msgs, channel=ch,
                               custom_cmd=cc, full_line=fl)
                        for p_, msgs, ch, cc, fl in found]
        reset_out_name()                 # 输出文件名跟着第一个模块的前缀
        refresh_list(0)
        load_module(0)
        messagebox.showinfo("导入完成",
                            "已导入 {} 个模块：\n{}".format(
                                len(found), "\n".join(f"  {p_}  ({len(m)} 条)" for p_, m, _c, _d, _e in found)))

    def clear_msgs():
        if messagebox.askyesno("确认", "清空当前模块的消息？"):
            txt.delete("1.0", "end")
            update_count()

    ttk.Button(f_tools, text="从 txt 导入", command=import_txt).pack(side="left", padx=2)
    ttk.Button(f_tools, text="从 cfg 导入（全部模块）", command=import_cfg).pack(side="left", padx=2)
    ttk.Button(f_tools, text="清空", command=clear_msgs).pack(side="left", padx=2)

    pw.add(left, weight=0)
    pw.add(right, weight=1)

    # ================= 全局设置 =================
    gf = ttk.LabelFrame(tab_gen, text="全局设置 / 输出")
    gf.pack(fill="x", padx=8, pady=(0, 8))
    gf.columnconfigure(1, weight=1)

    ttk.Label(gf, text="输出文件：").grid(row=0, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(gf, textvariable=v_out).grid(row=0, column=1, sticky="ew", pady=4)

    def browse_out():
        p = filedialog.asksaveasfilename(title="保存 cfg", defaultextension=".cfg",
                                         initialfile=os.path.basename(v_out.get()) or "my.cfg",
                                         filetypes=[("CFG 脚本", "*.cfg"), ("所有文件", "*.*")])
        if p:
            v_out.set(p)

    ttk.Button(gf, text="浏览…", command=browse_out, width=8).grid(row=0, column=2, padx=6)
    ttk.Button(gf, text="默认名", command=reset_out_name, width=8).grid(row=0, column=3, padx=(0, 6))
    ttk.Label(gf, text="（默认 = 第一个模块的前缀.cfg，可自己改）",
              foreground="#666").grid(row=0, column=4, sticky="w", padx=(0, 6))

    f_bind = ttk.Frame(gf)
    f_bind.grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=2)
    ttk.Checkbutton(f_bind, text="生成 bind 行  发消息键：",
                    variable=v_dobind).pack(side="left")
    ttk.Entry(f_bind, textvariable=v_bindkey, width=8).pack(side="left", padx=4)
    ttk.Label(f_bind, text="例：p / mouse4 / v").pack(side="left", padx=4)

    f_sw = ttk.Frame(gf)
    f_sw.grid(row=2, column=0, columnspan=3, sticky="w", padx=6, pady=2)
    ttk.Checkbutton(f_sw, text="启用模块切换（多模块时生效）  切换键：",
                    variable=v_switch).pack(side="left")
    ttk.Entry(f_sw, textvariable=v_switchkey, width=8).pack(side="left", padx=4)
    ttk.Label(f_sw, text="开关名：").pack(side="left", padx=(10, 0))
    ttk.Entry(f_sw, textvariable=v_switchname, width=12).pack(side="left", padx=4)
    ttk.Label(f_sw, text="活动指针名：").pack(side="left", padx=(10, 0))
    ttk.Entry(f_sw, textvariable=v_active, width=12).pack(side="left", padx=4)

    f_opt = ttk.Frame(gf)
    f_opt.grid(row=3, column=0, columnspan=3, sticky="w", padx=6, pady=2)
    ttk.Checkbutton(f_opt, text="自动转义引号/分号（推荐）", variable=v_escape).pack(side="left")
    ttk.Checkbutton(f_opt, text="每 10 条空一行", variable=v_blank).pack(side="left", padx=10)
    ttk.Checkbutton(f_opt, text="体内引号（照抄原文件风格）", variable=v_quote).pack(side="left", padx=10)
    ttk.Checkbutton(f_opt, text="UTF-8 BOM", variable=v_bom).pack(side="left", padx=10)
    ttk.Checkbutton(f_opt, text="CRLF 换行", variable=v_crlf).pack(side="left", padx=10)

    # ---- 预览页 ----
    prev_txt = tk.Text(tab_prev, wrap="none", font=("Consolas", 10))
    psb = ttk.Scrollbar(tab_prev, command=prev_txt.yview)
    prev_txt.configure(yscrollcommand=psb.set)
    psb.pack(side="right", fill="y")
    prev_txt.pack(fill="both", expand=True, padx=8, pady=8)

    def collect() -> Project:
        commit()
        proj.options = Options(
            do_bind=bool(v_dobind.get()), bind_key=v_bindkey.get().strip(),
            use_switch=bool(v_switch.get()), switch_key=v_switchkey.get().strip(),
            switch_name=v_switchname.get().strip(), active_name=v_active.get().strip(),
            escape=bool(v_escape.get()), quote_inside=bool(v_quote.get()),
            blank_every_10=bool(v_blank.get()), bom=bool(v_bom.get()),
            crlf=bool(v_crlf.get()))
        return proj

    def make_text(show_errors=True, notify_ignored=False, quiet=False, force=True):
        """生成 cfg 文本并显示在预览页。预览页里只有 cfg 原文，没有任何注释/标注。

        force=False 时，如果内容和上次完全一样就直接跳过——这样自动刷新
        （例如点了“预览”后页签事件) 不会重复弹窗。
        """
        p = collect()
        sig = repr([asdict(m) for m in p.modules]) + repr(asdict(p.options))
        if not force and sig == state.get("last_sig"):
            return None, [], []
        state["last_sig"] = sig
        errors, warnings = validate(p.modules, p.options)
        ignored = ignored_prompts(p.modules, p.options)
        prev_txt.delete("1.0", "end")
        if errors:
            if not quiet:
                if notify_ignored and ignored:      # 独立模式：即使出错也先把忽略的提示弹出来
                    messagebox.showinfo("独立模式：切换提示已忽略",
                                        _ignored_msg(ignored, "本次没有生成 cfg。"))
                if show_errors:
                    messagebox.showerror("无法生成", "\n".join(errors))
            return None, warnings, ignored
        text = build_cfg(p.modules, p.options)
        prev_txt.insert("1.0", text)                 # 只有 cfg 原文
        if not quiet and notify_ignored and ignored:  # 独立模式：只用弹窗提示
            messagebox.showinfo("独立模式：切换提示已忽略", _ignored_msg(ignored))
        return text, warnings, ignored

    def _ignored_msg(ignored, tail=""):
        body = "\n".join(
            "    {}  [{}]  {}".format(pre, "队伍麦 say_team" if mode == "say_team" else "控制台 echo", text_)
            for pre, mode, text_ in ignored)
        msg = ("当前是独立模式（没有模块切换），所以下面这些切换提示不会写进 cfg，"
               "只在这里提示你：\n\n" + body +
               "\n\n想让提示写进 cfg，请勾选“启用模块切换”。")
        if tail:
            msg += "\n\n" + tail
        return msg

    def do_preview():
        text, warns, _ = make_text(notify_ignored=True)   # 显式操作：总是重新渲染
        nb.select(tab_prev)
        if text is not None and warns:
            messagebox.showwarning("已生成，但有提醒", "\n".join(warns))

    def do_save():
        text, warns, _ = make_text(notify_ignored=True)
        if text is None:
            return
        path = v_out.get().strip()
        if not path:
            browse_out()
            path = v_out.get().strip()
            if not path:
                return
        try:
            real = write_cfg(text, path, collect().options)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        msg = f"已生成：\n{real}\n\n共 {len(proj.modules)} 个模块，" \
              f"{sum(len([x for x in m.messages if str(x).strip()]) for m in proj.modules)} 条消息。"
        if warns:
            msg += "\n\n提醒：\n" + "\n".join(warns)
        messagebox.showinfo("完成", msg)

    def do_copy():
        text, _, _ = make_text(notify_ignored=False)
        if text is None:
            return
        root.clipboard_clear()
        root.clipboard_append(text)
        messagebox.showinfo("已复制", "生成的 cfg 内容已复制到剪贴板。")

    def save_project():
        commit()
        p = filedialog.asksaveasfilename(title="保存项目", defaultextension=".json",
                                         filetypes=[("JSON 项目", "*.json")])
        if not p:
            return
        with open(p, "w", encoding="utf-8") as f:
            json.dump(collect().to_dict(), f, ensure_ascii=False, indent=2)
        messagebox.showinfo("已保存", p)

    def load_project():
        p = filedialog.askopenfilename(title="加载项目", filetypes=[("JSON 项目", "*.json")])
        if not p:
            return
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                obj = Project.from_dict(json.load(f))
        except Exception as e:
            messagebox.showerror("加载失败", str(e))
            return
        if not obj.modules:
            messagebox.showinfo("提示", "项目里没有模块。")
            return
        proj.modules = obj.modules
        proj.options = obj.options
        o = obj.options
        v_dobind.set(o.do_bind); v_bindkey.set(o.bind_key)
        v_switch.set(o.use_switch); v_switchkey.set(o.switch_key)
        v_switchname.set(o.switch_name); v_active.set(o.active_name)
        v_escape.set(o.escape); v_quote.set(o.quote_inside)
        v_blank.set(o.blank_every_10); v_bom.set(o.bom); v_crlf.set(o.crlf)
        reset_out_name()                 # 输出文件名跟着第一个模块的前缀
        refresh_list(0)
        load_module(0)

    f_btn = ttk.Frame(gf)
    f_btn.grid(row=4, column=0, columnspan=3, sticky="w", padx=6, pady=(8, 8))
    for text, cmd in (("① 预览", do_preview), ("② 生成并保存", do_save),
                      ("复制到剪贴板", do_copy), ("保存项目", save_project),
                      ("加载项目", load_project)):
        ttk.Button(f_btn, text=text, command=cmd, width=14).pack(side="left", padx=3)

    # ================= 说明页 =================
    help_txt = tk.Text(tab_help, wrap="word", font=("Microsoft YaHei UI", 10))
    hsb = ttk.Scrollbar(tab_help, command=help_txt.yview)
    help_txt.configure(yscrollcommand=hsb.set)
    hsb.pack(side="right", fill="y")
    help_txt.pack(fill="both", expand=True, padx=8, pady=8)
    help_txt.insert("1.0", HELP_TEXT)
    help_txt.configure(state="disabled")

    def on_tab(_e=None):
        # 点到“预览”页：内容有变化才重新渲染（避免点“预览”按钮时重复弹窗）
        try:
            if nb.index("current") == 1:
                make_text(notify_ignored=True, force=False)
        except Exception:
            pass

    nb.bind("<<NotebookTabChanged>>", on_tab)

    refresh_list(0)
    load_module(0)
    root.mainloop()


HELP_TEXT = """【这个工具是干什么的】

  把一串文本做成循环脚本：按一次你绑的键就发一条，发到最后一条自动绕回第一条。
  文本内容、指令、循环前缀、绑哪个键、要不要用一个键切换多套文案，全部自己定。


【生成器页 —— 左边：大循环模块列表】

  一个“模块”就是一套独立的循环（前缀 + 01、02、03…）。
  刚打开时列表是空的，点「新增模块」开始；一个模块就够用的话，就只建一个。

  · 新增模块 / 复制模块 / 删除模块 / 上移 ▲ / 下移 ▼
  · 「从 cfg 导入」可以把以前做好的循环脚本读回来，自动拆成各个模块继续改


【生成器页 —— 右边：当前模块设置】

  循环前缀     小条目的名字 = 前缀 + 序号。序号位数按条数自动决定，右侧会实时显示：
               不到 10 条 → 1 位（1、2、3）
               不到 100 条 → 2 位（01、02、10、33）
               100 条及以上 → 3 位（001、002、240）

  发送频道     全体麦 say / 队伍麦 say_team / 自定义指令
               · 选“自定义指令”后，右边框里写你要的指令，例如 say、say_team、echo、sm_say
               · 整行自定义（复选框）：消息框里每一行都写完整指令，不再自动加前缀，
                 可以用不同指令，分号也保留，所以一条里还能塞多条命令

  切换提示     切换模块时在游戏里提示一下当前是哪个模块：控制台 echo / 队伍麦 say_team / 不提示
  提示文本     上面那个提示的内容
  单独绑键     不使用模块切换时，给这个模块单独绑的键；留空则该模块不生成 bind 行
  后缀从 0 开始  序号从 0、1、2… 排，而不是从 1 开始
  消息内容     一行一条，空行会被忽略


【生成器页 —— 下面：全局设置 / 输出】

  输出文件     默认用第一个模块的前缀命名（前缀写 demo → demo.cfg），放在本脚本所在目录。
               可以自己改名或点「浏览…」选路径；一旦改过就不再自动跟随，
               想回到自动命名点一下「默认名」
  生成 bind 行 勾上才会写 bind 键 前缀；不勾就完全不写 bind 行
  启用模块切换 多个模块时，用一个键循环切换模块（默认右 Alt）。只有一个模块时自动按独立模式生成；
               多模块但不想用切换键 → 取消勾选，每个模块用自己的前缀和键
  自动转义     把会破坏脚本的英文引号、分号、// 自动换成安全字符
  每 10 条空一行 / UTF-8 BOM / CRLF 换行    排版与编码选项


【三个页签】

  生成器   所有设置都在这里
  预览     显示即将写进文件的原文，不带任何注释；提醒和错误一律用弹窗告诉你
  说明     就是本页


【右下角按钮】

  ① 预览          先看一眼生成结果
  ② 生成并保存    写出 cfg 文件（UTF-8 + CRLF，中文不乱码）
  复制到剪贴板    直接粘到别处
  保存项目        把当前所有模块和设置存成 json，下次「加载项目」接着改


【消息内容怎么写】

  · 普通模式（全体麦 / 队伍麦）：写指令后面的内容
        指令选 say、内容写「你好」        → 实际执行 say 你好
        指令选 say_team、内容写「收到」    → 实际执行 say_team 收到
  · 自定义指令：指令自己写，内容照常一行一条
        指令写 sm_say、内容写「你好」      → 实际执行 sm_say 你好
        指令写 say !drop、内容写「你好」   → 实际执行 say !drop 你好
  · 整行自定义：每行自己写完整指令，例如
        say 你好
        say_team 收到
        echo 测试
        say a; echo b          （一条里多条命令，仅整行自定义保留分号）

  · 分号：普通模式下英文分号会换成全角「；」，否则内容会被后面的循环语句截断
  · 英文引号 " 和连续斜杠 // 会自动换成安全字符——// 在脚本里等于注释，
    留着会把后面的循环语句吃掉，发完一条就卡住


【生成的文件怎么用】

  1. 放到游戏的 cfg 目录：
       CS2:   ...\\Counter-Strike Global Offensive\\game\\csgo\\cfg\\
       CS:GO: ...\\Counter-Strike Global Offensive\\csgo\\cfg\\
  2. 进游戏后在控制台执行它：exec 文件名
       例如文件叫 demo.cfg 就输入：exec demo.cfg
  3. 想手动换模块，控制台直接输入活动指针别名也行（默认 TextRider，后面跟模块前缀）
"""


# --------------------------------------------------------------------------- #
# 示例 / 命令行
# --------------------------------------------------------------------------- #
DEMO_TEXT = ["这是第一条示例", "这是第二条示例", "这是第三条示例", "这是第四条示例",
             "这是第五条示例", "这是第六条示例", "这是第七条示例", "这是第八条示例",
             "这是第九条示例", "这是第十条示例", "这是第十一条示例", "这是第十二条示例"]


def demo_project() -> Project:
    """示例项目：多模块 + 一个切换键（顺带演示 1 位 / 2 位 / 3 位后缀与三种提示）。"""
    mods = [
        Module(prefix="one", channel="say", prompt_mode="echo", prompt_text="进入 one",
               messages=["示例文本甲", "示例文本乙", "示例文本丙", "示例文本丁",
                         "示例文本戊", "示例文本己", "示例文本庚"]),
        Module(prefix="two", channel="say", prompt_mode="say_team", prompt_text="进入 two",
               messages=DEMO_TEXT),
        Module(prefix="three", channel="say_team", prompt_mode="none", prompt_text="",
               messages=[f"第 {i} 条示例文本" for i in range(1, 106)]),
    ]
    return Project(options=Options(), modules=mods)


def demo_custom() -> Project:
    """示例：自定义指令模式（指令自己写）与整行自定义模式。"""
    mods = [
        # 指令自己写：say_team
        Module(prefix="team", channel="custom", custom_cmd="say_team",
               messages=["示例文本甲", "示例文本乙", "示例文本丙"], bind_key="p"),
        # 指令自己写：echo
        Module(prefix="con", channel="custom", custom_cmd="echo",
               messages=["这是 echo 指令", "指令后面照样接内容"], bind_key="o"),
        # 整行自定义：每行都是完整指令，可以用不同指令，甚至一条塞多条命令
        Module(prefix="raw", full_line=True,
               messages=["say 你好", "say_team 收到", "say a; echo b",
                         "sm_say 自定义指令"], bind_key="l"),
    ]
    return Project(options=Options(use_switch=False, do_bind=False), modules=mods)


def run_cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CS 循环 alias CFG 生成器")
    ap.add_argument("--demo", action="store_true", help="生成示例 cfg 到 ./output 目录")
    ap.add_argument("--json", metavar="项目.json", help="按 JSON 项目文件生成")
    ap.add_argument("-o", "--out", metavar="输出.cfg", help="输出文件路径")
    ap.add_argument("--gui", action="store_true", help="强制打开图形界面")
    ap.add_argument("--version", action="version", version=VERSION)
    args = ap.parse_args(argv)

    if args.demo:
        base = os.path.dirname(os.path.abspath(__file__))
        outdir = os.path.join(base, "output")
        os.makedirs(outdir, exist_ok=True)
        proj = demo_project()
        single = Project(options=Options(use_switch=False, bind_key="p", escape=True),
                         modules=[Module(prefix="demo", channel="say", messages=DEMO_TEXT)])
        jobs = [("demo_单模块独立模式.cfg", single),
                ("demo_多模块_单键切换.cfg", proj),
                ("demo_自定义指令.cfg", demo_custom())]
        for name, p in jobs:
            errors, warnings = validate(p.modules, p.options)
            if errors:
                print("错误：\n" + "\n".join(errors))
                return 1
            text = build_cfg(p.modules, p.options)
            path = write_cfg(text, os.path.join(outdir, name), p.options)
            info = "、".join(f"{m.prefix}:{len(_clean_messages(m, True))}条"
                             f"(后缀{pad_width(len(_clean_messages(m, True)))}位)"
                             for m in p.modules)
            print(f"已生成 {path}\n    模块 {info}，共 {len(text.splitlines())} 行")
            for w in warnings:
                print("    提醒：" + w)
        return 0

    if args.json:
        with open(args.json, "r", encoding="utf-8-sig") as f:
            p = Project.from_dict(json.load(f))
        errors, warnings = validate(p.modules, p.options)
        if errors:
            print("无法生成：\n" + "\n".join(errors), file=sys.stderr)
            return 1
        text = build_cfg(p.modules, p.options)
        out = args.out or os.path.splitext(args.json)[0] + ".cfg"
        print("已生成 " + write_cfg(text, out, p.options))
        for w in warnings:
            print("提醒：" + w)
        return 0

    try:
        launch_gui()
    except ImportError as e:
        print("无法启动图形界面（缺少 tkinter）：", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(run_cli())
