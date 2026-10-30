# 系统文本载体与覆盖范围

## 盘点

在项目根目录执行：

```powershell
python -m solution.unpack.system_inventory
```

脚本按原版 `data.xp3` 索引选择解包工作区中的 `.tjs`、`.ks`、`.ini`、`.csv`、`.txt`，不会把 `scn_json/*.ins.txt` 等生成文件算作原版文本。只在内存解开文本包装和读取常量，原始文件不变。报告写入已忽略的 `solution/unpack/work/system_inventory.json`；默认不输出日文样本，附加 `--examples 2` 可为每个文件保留最多两项定位样例。

当前基线共 338 个文件，无缺失或解析失败：

| 类型 | 数量 | 实际载体 |
| --- | ---: | --- |
| `.tjs` | 41 | Kirikiri 类型 1 文本包装，解后为带 BOM 的 UTF-16LE |
| `.ks` | 30 | 同上 |
| `.ini` | 38 | 同上 |
| `.csv` | 7 | 同上 |
| `.txt` | 15 | 同上 |
| `.tjs` | 207 | `TJS2100` 编译字节码 |

本基线这些原始文件中未发现明文 UTF-8、CP932 或其他 Kirikiri 包装类型。生成的 SCN JSON 与指令源码另有编码，不能据此推断原版系统文件。

207 个字节码文件已提供 `DATA` 字符串常量表的导出和重建，不反编译指令。完整常量表共 43084 项；148 个文件包含合计 1317 条日文候选，混有显示文本、错误信息、字体、图层名和逻辑字符串，不能把候选数当作待译条目数。正式输入全部保持原文，未自动翻译或启用任何字节码常量。

## 可编辑载体中的边界

下面行号指解开类型 1 包装后的文本行号。

| 文件及位置 | 应关注的显示内容 | 必须保留的关联结构 |
| --- | --- | --- |
| `main/default.tjs` 第 46、92、94 行 | 历史记录的选项格式、未解锁音乐占位、高速跳过提示 | 字段名、`${text}`、样式和状态标识 |
| `main/default.tjs` 第 99–113 行 | `renderSampleConfig.text1/text2` 的文字速度预览；淡入菜单 `caption` | `id`、数值、表达式、样式键 |
| `main/help_opt.txt` | 配置悬停帮助正文 | 非缩进帮助键、空行结构及 `${...}` 表达式 |
| `main/soundlist.csv` | 音乐鉴赏标题列 | 第一列 BGM 文件标识 |
| `main/scenelist.csv` | 回想标题列 | 缩略图、脚本名、跳转标签、解锁标志 |
| `sysscn/brows_help.ks`、`brows_conf.ks` | 帮助正文、链接显示文字、设置分类 | KAG 标签、标签参数中的命令和目标标识 |
| `AppConfig.tjs` 第 5、9 行 | 游戏名、说明文档显示名 | GameId、URL、图标和文件路径 |
| `sysscn/speech_help.txt` 等 | 辅助功能的帮助正文，需确认本作实际入口 | 辅助功能命令、字典、应用路径 |

`main/Config.tjs` 用 `;System.title = ...` 这样的语句设置值；这里分号是语句分隔符，不能像 INI 注释一样整行丢弃。该文件大量日文是说明注释，字体名和文件名也不能按自然语言批量替换。

`default.tjs` 的 `voiceNames` 同时作为 `charvoice.csv` 的音量分组关联键；`GetComuIconName()`、`chvo_...` 等也依赖原角色名称。只改显示名字可能破坏索引或音量分组，当前应保留，后续用显示映射处理或验证关联的成组迁移。

`uipsd/*.ini` 中看似提示语的日文通常是 PSD/PIMG 图层路径。例如 `uipsd/dialog.ini` 的 `テキスト/終了しますか？`、`uipsd/opt_2text.ini` 的 `BTNS/#pos/テキストスピード` 都参与资源定位，不是独立文字值。图像汉化应保持路径与层名，通过静态资源清单显式覆盖相应图像。

`main/charlist.csv`、`charvoice.csv`、`imagenamemap.txt`、`envinit.tjs` 中的角色、表情、动作及资源名称主要承担逻辑匹配。字体许可文本、引擎版本和调试配置也不应作为普通界面文本批量导出。

## 容易遗漏的字节码内容

| 文件 | 当前可确认的候选内容 | 本轮边界 |
| --- | --- | --- |
| `sysscn/nocfchkdialog.tjs` | 返回标题、退出、存取、覆盖、快存快读、跳转和数据不整合确认，31 条日文常量 | 已验证确认提示常量的中文变长重建；正式输入仍为原文 |
| `sysscn/advancedmenu.tjs` | 高级设置菜单显示，2 条日文常量 | 已验证菜单常量的中文变长重建；正式输入仍为原文 |
| `system/MainWindow.tjs` | 窗口模式、存档、退出、字体选择、警告等，107 条日文常量 | 还包含调试和内部提示，需逐个判定用途 |
| `sysscn/system.tjs` | 快存快读反馈、返回开头、内存不足及加载错误等 | 同时包含大量内部逻辑与诊断字符串 |

`sysscn/syslangtext.tjs` 不是完整的界面语言表；其字符串表只有一条日文重复配置错误信息。不能仅替换该文件就宣称系统文字已汉化。

## 系统编辑接口

`system.json` 使用版本 2，提供三个互不混淆的编辑入口：

| JSON 字段 | 范围 | 默认状态 |
| --- | --- | --- |
| `records` | `default.tjs` 的五个已确认显示字段和 `help_opt.txt` 的 57 个正文行 | 保留原 62 个稳定标识，只改 `translation` |
| `documents` | 131 个可解码文本载体的整篇原文，包括 TJS、KS、CSV、INI、TXT | `enabled=false`，`translation=source` |
| `bytecode` | 207 个 TJS2 文件的全部字符串常量，保留常量索引 | 每项 `enabled=false`，`translation=source`，仅用 `candidate` 标记含日文项 |

整篇文档和字节码项只有同时满足 `enabled=true` 且译文确有变化时才产生覆盖；未启用却修改内容会报错。`candidate` 仅供定位，不会自动选入补丁。INI 图层路径、CSV 资源列、TJS 标识常量仍需人工确认，不因进入编辑 JSON 而成为显示文本。

```powershell
python .\main_system_text.py export
python .\main_system_text.py build
```

原文基线为 `game_script/raw_script/system/system.json`，可编辑输入为 `game_script/translated_script/system/system.json`。源摘要、文件路径、记录数量与顺序、常量索引、原文及候选标记都参与精确匹配守卫；只改 `translation` 和允许的 `enabled`。同一文件不能同时使用字段级 `records` 和整篇 `documents` 产生修改。

导出会保留已有版本 2 译文。旧版本 1 只有在内容精确等于未翻译基线时才能附加 `export --upgrade-source-only` 升级；升级前保存在 `tmp/system_schema_backup_时间戳.zip`，并核对备份字节。包含人工修改的旧输入会明确拒绝，不能用此选项覆盖译文。

独立 `build` 在 `solution/build/work/system_时间戳/` 只生成发生变化的原包相对路径文件，不修改静态资源或清单；原文输入会输出零个覆盖文件。`solution.script.system_text.rebuild(document)` 仍返回 `{原包相对路径: 重建字节}` 与修改数量，供主构建器逐个比较并只选择变化文件。

### 文本文档的保护范围

所有现有文本载体回写保持原类型 1 包装和 UTF-16LE 编码。CSV 保持行数、列数、第一列行键及注释行；其余列由使用者确认是否显示字段。INI 和 TJS 整篇接口是人工审查接口，不自动判定逻辑字段，也不执行 TJS 语法检查。

KS 整篇接口允许改动普通物理文本行，但保持 KAG 方括号标签、`@` 命令行、标签行、注释、脚本块和物理换行结构。标签内部的显示属性目前也按标签保护，不可直接改变。字段级帮助正文不能新增物理行。`${...}` 表达式与格式化占位符必须原样保留，包括表达式中嵌入的文字。

### TJS2 字符串表重建

`TJS2100` 头的偏移 8 为文件总长，偏移 12 开始为 `DATA` 块，块长度包含八字节块头。数字常量按一、二、四、八、八字节五张表存储，每表先记录四字节数量并按四字节对齐；之后是字符串数量及字符串记录，最后为字节数组常量表。

每个字符串记录使用四字节 UTF-16 代码单元数量、UTF-16LE 内容和四字节对齐填充。补充平面字符按两个代码单元计算。重建保持字符串数量及索引，更新每条长度、对齐、`DATA` 长度与文件总长；数字常量、字节数组常量、未改项的填充及后续整个 `OBJS` 块保持原字节。原文验证也会重新编码所有字符串记录，而非仅返回原文件。

`rewrite_bytecode(data, {常量索引: 新字符串})` 可单独重建并重新解析校验。正式 JSON 中例如 `bytecode["sysscn/nocfchkdialog.tjs"]["strings"][21]` 对应确认提示；启用并编辑前仍须按运行时用途确认，不能把所有含日文的常量批量替换。

## 验证与剩余边界

```powershell
python -m unittest solution.script.test_system_text
```

当前 338 个原始载体回环均逐字节一致，包含全部 207 个 TJS2 字符串表的真实重建。确认框和高级菜单的中文变长样本、补充平面字符、CSV 标题与帮助 KS 正文的回写通过结构重读；字节码样本保持全部其他常量、数字表、字节数组及 `OBJS` 字节不变。

逐文件原文回环摘要和两个字节码变长样本的结构保存证据记录在 `docs/system_text_validation.json`，不包含完整日文字符串转储。

正式输入没有应用测试译文。上述结果证明编辑回填与结构保存能力，不替代每个显示入口的语义确认、TJS2 修改后的实际游戏加载或整篇 TJS 的语法验证。图像内文字仍走静态资源清单流程；系统编辑能力齐全不代表系统文字已全部翻译。
