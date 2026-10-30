# dc4_fd_chineseization

《D.C.4 - Fortunate Departures -》的可重复构建汉化技术工程，面向本项目记录的 Windows x86 原版。

## 当前状态

已实现本作 Cx 解密、XP3 解包、SCN 双行文本回填、系统文本和 TJS2 字符串表重建、PIMG 图片编辑、运行时源码构建，以及补丁封包与独立游戏副本验证。

正式初始译文全部保持日文，图片静态清单初始为空；当前产物是汉化技术工程，尚非全篇汉化发布版。`--smoke-sample` 使用独立技术样例，不修改正式译文。

| 范围 | 已验证结果 |
| --- | --- |
| 原版基线 | 本地 62 个文件，KiriKiri Z x86，主程序版本 `1.2.0.3` |
| 原版资源 | 5 个 XP3 的 20,001 个保护资源完整解密并通过 Adler-32；排除 5 个尺寸异常的保护告示 |
| SCN | 215 个脚本、28,860 个稳定文本 ID、60,726 个引用；全部回编再反编译结构等价 |
| 系统载体 | 338 个文件：131 个包装文本、207 个 TJS2；原文重建逐字节一致 |
| TJS2 编辑 | 完整导出 43,084 个字符串常量，默认全部 `enabled=false`，需逐项确认用途后启用 |
| PIMG | 23 个文件、448 张内部图片、1,029 个图层；原图回编的结构与 RGBA 像素一致 |
| 实机样例 | 已验证标题加载、中文正文、历史记录、存档保存与读回、多行文字速度样例、TJS2 中文菜单 |

TJS2 字符串变长回填后，实机菜单已显示“文件(F)”。确认框仍保留原文；常量表结构重建通过不等于每个系统入口已翻译或显示正确。SCN 和 PIMG 经过 FreeMote 重新序列化，验证的是完整结构、文本或像素等价，不宣称二进制逐字节一致。

## 依赖

- Windows、Python 3.10 或更高版本；Python 包版本见 `requirements.txt`。
- Visual Studio C++ 构建工具的 MSVC x86 编译器和 Windows SDK，构建器通过 `vswhere` 或标准安装位置定位。
- 项目本地 `tools/Ulysses-FreeMoteToolkit-v4.3.0/`，保留其 `lib/`、插件、配置和许可证；工具使用 .NET Framework 4.8。
- Windows 字体 `Microsoft YaHei`；可在 `solution/runtime/config.ini` 修改字体配置。FFmpeg 仅用于可选媒体解码验证。

```powershell
python -m pip install -r .\requirements.txt
```

SCN/PSB 使用 Unicode UTF-8，系统文本包装与 TJS2 字符串使用 UTF-16LE，不需要全局 GBK 替换。字体选择与脚本编码分别处理。

## 首次构建

所有命令均在本项目根目录执行。先将同版本原版放入 `game/`，保留原有目录结构；本地输入及完整副本不提交。

```powershell
# 核对本作密钥材料，完整解包并反编译 SCN。
python .\main_key.py --check
python .\main_unpack.py .\game .\solution\unpack\work --verify-cache --decompile

# 导出正式文本；已有译文只校验，不覆盖。
python .\main_scn_text.py export .\solution\unpack\work\data\scn_json
python .\main_system_text.py export

# 预览旧生成物清理，再构建补丁包与完整验证副本。
python .\main_build.py --dry-run
python .\main_build.py
```

默认构建先清理本构建器已认证的旧 `out/build_*`、`out/package_*`、`out/release_*`，再创建本轮独立输出。陌生文件、修改过的普通生成文件、无归属标记目录及输入缓存会保留。`--dry-run` 仅检查并打印计划；`--keep-previous-outputs` 用于调试保留旧输出。

默认构建不启动游戏。需要独立技术样例和自动标题加载检查时：

```powershell
python .\main_build.py --smoke-sample --run-smoke
```

`--run-smoke` 到达标题资源检查点后关闭本轮游戏；附加 `--leave-running` 才保留成功运行的进程，且必须同时使用 `--run-smoke`。`--no-release` 只构建补丁和中间产物，不创建完整游戏副本，不能与 `--run-smoke` 同用。自动标题检查不能替代人工检查正文、菜单、存档和字体。其他参数、输出布局和清理边界见 [构建与发布](docs/build.md)。

## 增量维护

正文、人名、选项及电话消息编辑 `game_script/translated_script/scn/*.ks.txt` 的 `●` 行，保留 ID、`○` 源文和控制标记，然后执行 `python .\main_build.py --no-release` 生成并验证补丁，或不加该选项同时生成完整验证副本。逻辑人名和资源键保持原值，可见人名通过原生显示覆盖字段写入，详见 [SCN 结构](docs/scn.md)。

系统文字编辑 `game_script/translated_script/system/system.json`。已有 62 个确认显示字段可直接修改 `translation`；整篇文档和字节码常量须明确设为 `enabled=true` 后再修改，严禁批量替换资源名、角色关联键或表达式。可先单独执行 `python .\main_system_text.py build` 验证，再执行完整构建，详见 [系统文本](docs/system_text.md)。

图片采用显式导出、重封、发布流程：

```powershell
python .\main_pimg.py export uipsd/window.pimg
# 编辑 solution/patch/work/images/uipsd/window.pimg/ 中的 PNG。
python .\main_pimg.py build uipsd/window.pimg
python .\main_pimg.py publish uipsd/window.pimg
python .\main_build.py --no-release
```

仅 `publish` 将确有修改且验证通过的图片加入正式静态资源及清单；完整构建只读取该清单，不会自动打包整个编辑目录。细节见 [PIMG 图片](docs/images.md)。

## 目录

| 路径 | 用途 |
| --- | --- |
| `game/` | 本地原版副本，复制时排除现存 `savedata/`，不提交 |
| `game_script/raw_script/` | 正式日文文本基线 |
| `game_script/translated_script/scn/` | 正式双行正文译文 |
| `game_script/translated_script/system/system.json` | 正式系统文本及字节码字符串译文 |
| `solution/decrypt/` | 经本作验证的解密实现与参数 |
| `solution/unpack/work/` | 原版解包与反编译缓存，不提交 |
| `solution/script/` | SCN 与系统文本维护实现 |
| `solution/build/` | 脚本回写、封包重建、构建与验证实现 |
| `solution/runtime/` | 所需运行时补丁的源码与构建入口 |
| `solution/patch/base/chs_patch/` | 维护的静态补丁资源 |
| `solution/patch/base/chs_patch_manifest.txt` | 静态资源进包清单，初始为空 |
| `solution/patch/work/` | 图片编辑与验证工作区，不提交 |
| `docs/` | 原版基线、格式和维护文档 |
| `tmp/` | 解包、调查和验证的中间产物，不提交 |
| `out/build_*` | 本轮脚本、运行时、封包复核和构建摘要 |
| `out/package_*` | 可复制到同版本原版的补丁文件，不含原游戏 |
| `out/release_*` | 排除原有存档的完整验证副本，不提交 |

维护流程以项目根目录为基准使用相对路径。游戏、工具缓存、生成物与维护源码分开保存。

## 结构与来源

- [汇总验证记录](docs/validation.json)
- [构建、发布与清理](docs/build.md)
- [原版文件基线](docs/game_baseline.json)与[全部资源校验](docs/archive_validation.json)
- [Cx 参数与来源](docs/decryption.md)、[XP3 结构](docs/xp3.md)
- [SCN 结构与双行文本](docs/scn.md)、[系统文本与 TJS2](docs/system_text.md)
- [PIMG 图片流程及 FreeMote 固定来源](docs/images.md)
- [运行时构建与字体](docs/runtime.md)、[第三方源码及许可证](solution/runtime/THIRD_PARTY.md)

维护源码、正式文本、清单和许可归属进入版本管理；原版、个人存档、工具缓存、构建 EXE/DLL/XP3 和完整验证副本不提交。
