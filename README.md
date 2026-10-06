# 循环 alias CFG 生成器

一个带图形界面的小工具：把一串文本做成「按一次键发一条、发完自动绕回第一条」的
Source 引擎配置脚本（CS2 / CS:GO）。文本内容、指令、循环前缀、绑键方式都能自己定。

## 功能

- **图形界面**，只用 Python 标准库（tkinter），不需要装任何第三方包
- **多个循环模块**：每个模块是一套独立循环，条目名 = 前缀 + 序号
- **序号位数自动**：不到 10 条 → 1 位（1、2、3）；不到 100 条 → 2 位（01、33）；100 条及以上 → 3 位（001、240）
- **一个键切换所有模块**（默认右 Alt），切换时可选在游戏里提示当前是哪个模块
- **四种指令写法**（每个模块独立选）
  - 全体麦 `say`
  - 队伍麦 `say_team`
  - 自定义指令：`say` 这部分自己写，例如 `say !drop`、`sm_say`、`echo`
  - 整行自定义：消息框里每行都写完整指令，可以用不同指令，分号也保留
- **绑键开关**：勾上就写 `bind 键 前缀`，不勾就完全不写 bind
- **反向导入**：从 txt（一行一条）或已有的循环 cfg 读回所有模块继续改
- **项目保存 / 加载**：当前模块和设置存成 json，下次接着改
- 输出 UTF-8（可选 BOM）+ CRLF，中文不乱码

## 环境要求

Python 3.8 或更高版本（Windows 官方安装包自带 tkinter）。

## 使用

```bash
py cfg_generator.py                               # 打开图形界面
py cfg_generator.py --demo                        # 生成示例 cfg 到 ./output 目录
py cfg_generator.py --json 项目.json -o 出.cfg    # 按 json 项目文件生成
```

界面分三个页签：**生成器**（所有设置）、**预览**（即将写进文件的原文）、**使用说明**。
点「① 预览」检查，点「② 生成并保存」写出文件。输出文件默认用第一个模块的前缀命名。

## 生成的文件长什么样

独立模式（一个模块自己循环）：

```cfg
alias "demo01" "say 你好; alias demo demo02";
alias "demo02" "say 大家好; alias demo demo03";
alias "demo12" "say 再见; alias demo demo01";

alias demo "demo01"

bind p demo
```

多模块 + 一个切换键：

```cfg
alias "one01" "say 你好; alias one one02";
alias "two01" "say 收到; alias two two02";

alias "modesw_m1" "echo 进入 one; alias TextRider one; alias one one01; alias modesw modesw_m2";
alias "modesw_m2" "echo 进入 two; alias TextRider two; alias two two01; alias modesw modesw_m1";

alias modesw "modesw_m1"
alias TextRider "one"
alias one "one01"
alias two "two01"

bind p TextRider      ← 发消息键：发“当前模块”的下一条
bind RALT modesw      ← 切换键：循环切换所有模块
```

## 放进游戏

1. 把生成的 cfg 放到配置目录：
   - CS2：`...\Counter-Strike Global Offensive\game\csgo\cfg\`
   - CS:GO：`...\Counter-Strike Global Offensive\csgo\cfg\`
2. 进游戏后在控制台执行：`exec 文件名`

## 说明

- 消息里的英文分号在普通模式下会换成全角「；」，否则内容会被后面的循环语句截断；
  自定义指令 / 整行自定义模式下分号保留，可以一条里写多条命令。
- 英文引号 `"` 和连续斜杠 `//` 会自动换成安全字符——`//` 在脚本里等于注释，
  留着会把后面的循环语句吃掉，发完一条就卡住。
- 详细用法见程序里的「使用说明」页签。

## 目录

```
cfg_generator.py    主程序（单文件，含界面）
output/             --demo 生成的示例
```
