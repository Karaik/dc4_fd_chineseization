# SCN 结构、文本维护与回环验证

本作 `data.xp3` 中 `scn/` 的 215 个 `.ks.scn` 解密后均为 `PSB\0`、版本 3。负载提取先逐文件核对 XP3 的 Adler-32，再交给 FreeMote。原始字符串是 UTF-8：直接检查字符串表可见日文的 UTF-8 字节，FreeMote 默认编码可正确反编译。本链路以 UTF-8 回写，不引入 `cp932`、`gbk` 或编码过滤表。

## 真实结构

顶层字段固定为 `hash`、`name`、`outlines`、`scenes`。本作共 390 个场景、27,198 个正文块。场景可能没有正文，也可能包含跳转、求值和选择结构，不能只遍历第一个场景。

| 位置 | 含义与处理 |
| --- | --- |
| `scenes[*].texts[*][0]` | 逻辑发言人及电话层路由标识，保持原值 |
| `scenes[*].texts[*][1][*][0]` | 覆盖显示的人名；原值为空时从常规逻辑发言人导出可译名，修改后仅写入此覆盖字段 |
| `scenes[*].texts[*][1][*][1]` | 含换行、注音的正文；图片块则是受保护的图片资源标记 |
| `scenes[*].texts[*][1][*][2]` | 去注音及换行后的显示 UTF-16 单元数，修改正文后重算 |
| 正文块可选的 `[3]` | 去换行、采用读音的缓存，修改正文后重算 |
| 正文块可选的 `[4]` | 去换行、保留基字的历史缓存，修改正文后重算 |
| `scenes[*].selects[*].text` | 选项正文；`target`、`storage`、`eval` 保留 |
| `scenes[*].title` | 场景标题 |
| `lines` 中 `startline` 的 `name` | 逻辑发言人或电话层路由标识，保持原值 |
| `lines` 中 `autosave` 的 `text` | 自动存档说明 |
| `lines` 中 `comumode` 的 `text` | 电话界面的消息正文 |
| 数字指令状态及正文状态中的 `comumode[*].text` | 电话消息的状态副本，同源文字同时回填 |

正文块之外的声音 `name`、`voice`、层 `name`、`icon`、`imageFile`、服装、表情、姿势、位置名、脚本名、标签、求值字符串及 `runLineStr` 是资源或执行标识，保持原值。不能把所有含日文的字符串一律作为可见正文替换。

本作有 911 个现成人名覆盖块，包含 `？？？`、`みんな` 等覆盖逻辑名的实例。常规人名翻译使用同一原生覆盖机制，所有逻辑发言人、`startline`、声音和层标识都不改。电话正文、电话指令及所有状态副本共用同一 `message` ID，避免当前显示已翻译而恢复状态仍是日文。

正文块的实际长度为 3、4 或 5 项。27 个独立图片块使用 `%i1&comu_stamp033;` 一类标记，显示长度为 1，第四项为可译的无障碍说明 `スタンプ画像`；标记本身不可更改。

## 正文标记

- `\n` 是两个字符组成的 SCN 换行标记。译文可移动它，但数量必须相同；JSON 字符串中表现为 `\\n`。
- `[けい]稽` 表示单字注音；`[ホームルーム,1]ＨＲ` 的数字表示“额外基字数”，共覆盖 2 个基字。
- `[・]俺` 是强调注音，读音缓存仍采用基字。读音中的 `・` 在读音缓存中去掉。
- 注音是可编辑显示内容，可重新编写或移除完整注音段；不接受缺括号、缺基字或非法长度。
- 不接受物理换行、NUL、未知反斜杠控制符或未知图片资源标记。

解析器已对全部 27,171 个非图片块重新计算显示长度及存在的读音、历史缓存，与本作原值逐项一致。修改正文时只重算该块的现有派生字段，不扩展块的结构。

## 双行文本

正式日文基线为 `game_script/raw_script/scn/`，正式可编辑译文为 `game_script/translated_script/scn/`。每个 `.ks.scn` 对应一个 `.ks.txt`：

```text
# body; occurrences=1
○0123456789abcdef01234567○"日文原文"
●0123456789abcdef01234567●"中文译文"
```

两行的值都是 JSON 字符串，保留引号和转义。注释中的 `occurrences` 是引用位置数；普通正文、选项、标题、自动存档说明和图片说明逐真实出现独立导出，即使源文相同，也可按上下文分别翻译。`source_occurrence` 以 1 为起点标明同一脚本、角色、源文的出现次序。

ID 取脚本相对文件名、角色、源文及从 0 开始的同文出现序号所组成 UTF-8 内容的 SHA-256 前 24 位，各部分以 NUL 分隔。序号为 0 时省略序号及其分隔符，保留首次出现的原有 ID。插入其他不同源文不会改变已有条目的 ID；在同文前新增一次出现会顺延后续同文的序号，源文版本迁移时仍需核对上下文。ID 不依赖场景或正文的绝对数组下标。

同一脚本内，人名仍按源显示名统一，电话消息仍按源文合并实际正文、指令和状态快照；这些记录没有 `source_occurrence`，一个 ID 回填全部已确认引用位置。普通正文不会与其他位置的同文合并，也不会与电话消息按字面内容自动联动。

回填拒绝重复 ID、未知 ID、缺项、源文不一致、译文缺文件或多余文件。默认导出只创建尚不存在的译文；已有译文仅验证，绝不重写。已存在的日文基线若与当前源文不同也会中止，需要先人工处理版本迁移。

完整导出得到 28,860 个 ID，覆盖 60,726 个引用位置：正文 26,887、人名 875、电话消息 527、标题 390、自动存档说明 133、选项 21、图片说明 27。上下文拆分保留原有 28,421 个首次出现 ID，新增 439 个独立上下文 ID。初始正式译文全部等于日文源文；中文冒烟输入使用独立目录。

## 工具与来源

解析器、文本格式、覆盖规则和验证器维护于 `solution/script/scn.py`，结构依据为本作已解密原始 SCN 及其 FreeMote 输出，没有套用其他作品的数组下标。PSB 的二进制反编译和回编委托给项目内 `tools/Ulysses-FreeMoteToolkit-v4.3.0/`。

上游为 [FreeMote](https://github.com/UlyssesWu/FreeMote)，遵守工具目录保留的 `FreeMote.LICENSE.txt`。本地两个入口的文件及产品版本均为 `4.3.0.0`：

| 文件 | SHA-256 |
| --- | --- |
| `PsbDecompile.exe` | `98d0805fe9a59b0913a4ccaec69be51291c974ec9420ab86fed48d75c5702519` |
| `PsBuild.exe` | `650875846ffa65093a9c258cbdad7c3da834e7276480139e37d4cf9a486be618` |

反编译缓存同时保留 `.json`、`.resx.json`、`.ins.txt`。可读指令源逐行显示场景元数据、`LINE` 指令及 `TEXT` 状态，与 JSON 一一对应；构建以 JSON 为结构基础、双行文件为译文输入。缓存清单绑定原始 SCN、JSON、元数据和工具的 SHA-256，未知或陈旧的额外 JSON 会中止处理。

## 命令与接口

以下命令以项目根目录为基准，先运行项目解包入口取得 `solution/unpack/work/data/scn/`：

```powershell
python .\main_scn_text.py decompile .\solution\unpack\work\data\scn .\solution\unpack\work\data\scn_json
python .\main_scn_text.py export .\solution\unpack\work\data\scn_json
python .\main_scn_text.py compile .\solution\unpack\work\data\scn_json .\out\scn_check
python -m unittest discover -s .\tests -p test_scn.py -v
```

`compile` 的输出目录必须为空。它只生成 SCN，不复制完整游戏、不启动游戏、不自动清理旧输出；完整游戏构建由 `main_build.py` 处理受控清理。编译工作目录位于输出的同级隐藏目录，保留编译日志、预期 JSON、重新反编译 JSON 和 `roundtrip_summary.json`。重复检查可改用新的独立输出目录。

构建器可以直接调用：

```python
from solution.script.scn import ensure_decompile, export_text, compile_translations

ensure_decompile(input_scn_dir, output_json_dir, tool)
export_text(output_json_dir, raw_dir, translated_dir)
result = compile_translations(output_json_dir, translated_dir, outbin, tool, verify=True)
```

参数均接受 `Path`；`tool` 是工具目录或其中一个工具路径。所有返回值为可写入 JSON 审计的字典。编译返回 `files`、`verified`、`changed_records`、`binary_files`、`work`、`verification_json`。它先验证全套译文，再启动回编；重新反编译后比较完整 JSON 树，包括未修改的资源、指令、结构、文本及缓存。只允许数值相等的整数/浮点表示差别。

## 已验证范围

本轮全量原文回环为 215/215，完整 JSON 树等价，正式译文修改数为 0。215 个二进制均与原始文件字节不同，不能宣称逐字节回环：FreeMote 重新序列化 PSB 的表及布局，长度和偏移由编译器重建。代表样本从 226,406 字节变为 240,368 字节，重新反编译的完整结构相等。

回归使用本作实际三个 SCN，覆盖普通正文、注音缓存、电话、选择分支；中文正文变长、人名、去注音后的缓存、选项均经过回编再反编译，并核对逻辑人名、声音名与跳转标签保留。`4fd_com191125d` 两个相同注音正文分别回填不同中文，并检查各自长度、读音、历史缓存；只修改一次出现时，另一处和所有非目标结构保持原值。另检验插入无关正文后 ID 稳定，以及电话正文与全部状态副本同步。重复/缺失/陈旧 ID、控制码丢失、错误显示长度会中止。正式译文不保留测试中文。

这些结果证明资源和脚本层的可编辑回环。实际游戏加载、字体、中文显示及交互行为由整包冒烟单独验证，不能由 JSON 比较替代。
