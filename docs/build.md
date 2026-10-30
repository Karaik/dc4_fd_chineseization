# 构建、发布与增量维护

## 输入与依赖

所有命令以本作项目根目录为基准。`game/` 是本地同版本原版输入，基线记录 62 个文件；不向该目录回写补丁或测试存档。项目只维护汉化技术链，正式初始译文仍全部等于日文源文。

依赖 Windows、Python 3.10 以上、`requirements.txt` 中的 `pefile` 和 Pillow、MSVC x86 C++ 构建工具及 Windows SDK。运行时构建通过 `vswhere` 和标准安装位置寻找编译环境。SCN 与 PIMG 使用本地 `tools/Ulysses-FreeMoteToolkit-v4.3.0/`，需保留完整 `lib/` 和工具配置；其目标运行时为 .NET Framework 4.8。FFmpeg 仅供 `python -m solution.decrypt.verify` 的可选媒体解码核验，普通构建不调用它。

FreeMote 固定版本和永久源码链接见 [图片文档](images.md)，解密参考来源见 [解密实现说明](../solution/decrypt/README.md)，运行时源码和许可证见 [第三方归属](../solution/runtime/THIRD_PARTY.md)。

| 输入 | 用途 |
| --- | --- |
| `game/DC4_FD.exe` 与原版 XP3 | 经摘要核对的引擎和资源基线 |
| `solution/decrypt/key.json`、`control_block.bin` | 从本作 EXE 提取的 Cx 参数 |
| `game_script/translated_script/scn/` | 全套正式 SCN 双行文本 |
| `game_script/translated_script/system/system.json` | 显示字段、整篇文档与 TJS2 常量的显式译文输入 |
| `solution/patch/base/chs_patch/` | 人工维护或图片发布生成的静态覆盖资源 |
| `solution/patch/base/chs_patch_manifest.txt` | 允许进入补丁的静态资源完整相对路径 |
| `solution/runtime/src/`、`config.ini` | 运行时维护源码及字体配置 |

## 首次准备

```powershell
python -m pip install -r .\requirements.txt
python .\main_key.py --check
python .\main_unpack.py .\game .\solution\unpack\work --verify-cache --decompile
python .\main_scn_text.py export .\solution\unpack\work\data\scn_json
python .\main_system_text.py export
```

`main_key.py --check` 从本作 EXE 重新提取并对比已维护的参数，不改写材料；不带该选项则重新生成参数文件。不同 EXE 摘要会拒绝继续，不能把本作参数直接套到其他版本。

`main_unpack.py` 不带位置参数时也使用上述原版和缓存根。解包核验完整资源的 Adler-32，输出保留归档目录，例如 `solution/unpack/work/data/scn/`。5 个 XP3 共 20,006 个索引项，其中 5 个非保护告示的声明尺寸异常，明确排除；其余 20,001 个保护资源均已完整验证。

只需检查目录信息时可使用 `--index-only`；该选项不解密负载、不反编译，不能当作解密验证。`--overwrite` 仅用于明确重提取已有缓存资源，默认不会静默覆盖不同内容。

## 完整构建入口

```powershell
python .\main_build.py --dry-run
python .\main_build.py
```

构建顺序为：检查并清理本构建器旧输出；准备 `data.xp3` 缓存；校验或反编译 SCN；核对正式文本；回填并重新反编译全部脚本；读取静态清单；重建明确修改的系统载体；从源码编译运行时；封装补丁；重新解包补丁并逐文件比对；生成补丁目录和完整验证副本；写入构建摘要及归属清单。

默认构建不启动游戏，也不自动进行交互验证。`pipeline_summary.json` 保留脚本回环、系统覆盖列表、运行时构建、包重读结果和各输出路径，不能仅凭终端的完成提示判断实际菜单、剧情或存档全部通过。

| 参数 | 实际行为 |
| --- | --- |
| `--dry-run` | 只打印旧生成物清理与保留计划，随即结束，不生成本轮副本或补丁 |
| `--keep-previous-outputs` | 调试时保留旧输出，其余校验和本轮独立输出照常执行 |
| `--no-release` | 生成构建目录和补丁目录，跳过完整游戏副本 |
| `--verify-cache` | 在通常的大小、修改时间检查之外，逐资源重新校验解包缓存 SHA-256 |
| `--translation-dir <目录>` | 指定一套完整的 SCN 译文目录，仍受全部 ID 和源文守卫约束 |
| `--system-translation <文件>` | 指定系统文本 JSON，默认使用正式输入 |
| `--psb-tools <目录>` | 指定完整的项目内 FreeMote 工具目录 |
| `--smoke-sample` | 从当前输入制作本轮独立技术样例，加入中文首句、速度预览及指定系统菜单常量；不改正式输入 |
| `--run-smoke` | 在新建完整副本中启动游戏，检查运行时日志和标题资源读取；成功后默认关闭本轮进程 |
| `--leave-running` | 仅与 `--run-smoke` 同用，成功时保留本轮游戏供人工检查；失败仍停止本轮进程 |

`--run-smoke` 需要完整副本，不能与 `--no-release` 同用。`--smoke-sample` 本身不会启动游戏，`--run-smoke` 本身也不会自动把正式日文改成中文。

```powershell
# 只做技术样例封包与结构验证。
python .\main_build.py --smoke-sample --no-release

# 创建独立游戏副本，检查标题加载后退出。
python .\main_build.py --smoke-sample --run-smoke

# 创建并保留成功启动的本轮游戏，供人工检查。
python .\main_build.py --smoke-sample --run-smoke --leave-running
```

自动检查先运行启动器的 `--check --no-dialog`，再启动游戏并寻找标题资源读取检查点；遇到运行时错误、提前退出或超时会失败。它验证标题启动路径，不代替后续剧情、所有菜单、字体覆盖或存档操作验收。

## 输出与补丁布局

每轮使用新的时间戳和随机标识。默认产生三个相互独立的目录：

```text
out/
  build_<run_id>/
    scn_bin/                 回编后的 SCN
    .scn_bin_work_<编号>/    编译日志、预期及重新反编译 JSON
    patch/                   本轮最终入包资源
    patch_roundtrip/         从生成 XP3 重读的验证资源
    runtime/                 本轮源码编译与运行时自测产物
    smoke_translations/      仅技术样例模式存在
    pipeline_summary.json
    .vn-build-output.json
  package_<run_id>/
    DC4_FD_chs.exe
    dc4_fd_chs.dll
    chs_patch/
      patch.xp3
      config.ini
    readme.txt
    THIRD_PARTY.md
    LICENSE.minhook.txt
    LICENSE.krkrz.txt
    .vn-build-output.json
  release_<run_id>/
    原版游戏文件及 plugin/
    上述补丁文件
    savedata_chs/            启动游戏后按需产生
    chs_patch/launcher.log
    chs_patch/runtime.log
    .vn-build-output.json
```

`package_*` 只包含补丁和许可说明，不含原游戏。将其中补丁文件复制到同版本原版目录后运行 `DC4_FD_chs.exe`；`.vn-build-output.json` 是本地生成物归属标记，不是运行依赖。`release_*` 是用于本地验收的完整副本，复制原版时排除 `savedata/`、`savedata_chs/`。默认不创建额外验证游戏副本。

原版 EXE、XP3、DLL 和签名文件保持原字节。启动器显式加载自编译 DLL，运行时只从 `chs_patch/patch.xp3` 精确覆盖命中资源；未命中的资源走原版。补丁包使用未压缩索引、未压缩未加密单段资源，并验证每项 Adler-32；避免原版 Cx 过滤器对新补丁重复处理，见 [运行时结构](runtime.md)。

## 静态资源与冲突

静态清单每行一个原归档相对路径，支持空行和 `#` 注释，例如 `uipsd/window.pimg`。只读取清单列出的普通文件；路径越界、文件本身为符号链接、缺失、大小写重复路径或把 EXE、DLL、XP3 当作静态输入都会失败。

SCN 始终从正式或指定译文编译后加入 `patch/scn/`。系统文本仅将与原版字节确有差异的载体加入补丁。若静态资源占用了对应生成路径，构建明确报告冲突；不会按复制顺序静默覆盖。静态清单不承担脚本、系统文字或运行时二进制的替代构建职责。

完整封包后重新解包，比较所有入包相对路径和 SHA-256，数量或内容不一致即失败。编译工作缓存只用于验证，不通过递归扫描混入补丁。

## 缓存与译文保护

解包缓存位于 `solution/unpack/work/`，清单绑定原版封包 SHA-256、解密实现及密钥指纹、条目数量、资源大小和修改时间。`--verify-cache` 进一步重算每个资源摘要。原版和解密方案不匹配、缓存缺失或被修改时不直接接受旧结果。

SCN 缓存清单绑定原始脚本、JSON、元数据和 FreeMote 工具摘要；变更时重新反编译，未知额外 JSON 会中止。正式原文基线和已有译文默认不覆盖。回填拒绝缺文件、额外文件、重复或未知 ID、源文不匹配和控制标记缺失；字数、注音读音和历史缓存由工具重算，再比较完整反编译 JSON。

系统 JSON 只允许编辑指定 `translation`、`enabled` 字段，其他源摘要、路径、顺序和常量索引保持。整篇文档与字段级编辑不能同时修改同一文件。207 个 TJS2 共 43,084 个常量默认禁用，含日文候选也可能是资源键或逻辑字符串，必须按 [系统文本边界](system_text.md) 逐项确认。

## 构建前清理与失败目录

清理只处理 `out/` 下由 `dc4-fd-localization` 创建、名称可识别、项目归属和实际文件清单一致的直接子目录。它先检查完整计划，再由同一 Python 进程执行删除；拒绝项目根、原版、正式文本、静态资源、缓存输入及任何链接、联接点或重解析点。不会整删 `out/`，也不会给未知旧目录补写标记。

普通生成文件增加、删除或被修改时，整个目录保留并报告原因。一次性 `release_*` 中仅 `savedata_chs/**`、`chs_patch/launcher.log`、`chs_patch/runtime.log` 登记为可变位置，因此测试存档会随该验证副本清理。需要保留的测试存档应先存到这些一次性输出之外；原版及用户原有存档不属于此登记范围。

本轮输出通过独占创建的上下文登记。普通构建异常会原样抛出，同时封存已产生的实际文件清单；下一轮可按同样边界清理完整或部分失败产物。强杀、断电或目录身份变化可能无法封存，此类无有效标记目录按未知内容保留。清理失败会终止构建，不带着不完整清理结果继续复制游戏。实现约束见 [清理模块说明](../solution/build/cleanup.md)。

## 增量步骤

正文只修改 `game_script/translated_script/scn/` 的译文行，然后执行 `python .\main_build.py --no-release`；需要实机验证时执行普通完整构建并运行本轮启动器。重新导出会保留已有译文，不能靠重新导出强行接受源文版本差异。逻辑人名、声音名和资源键不翻译，可见人名由原生显示覆盖字段承接。

系统文字修改 `game_script/translated_script/system/system.json`。先运行 `python .\main_system_text.py build` 做独立载体回写检查，再完整构建。默认原文输入输出零个系统覆盖文件；确认后的整篇或字节码修改必须同时启用对应项目。

图片先执行 `python .\main_pimg.py export uipsd/window.pimg`，在独立编辑目录修改 PNG，再执行 `build` 和显式 `publish`。只有通过结构、层序、资源共享和 RGBA 像素验证且相对基线确有变化的资源会进入正式静态清单；最后运行完整构建。详见 [图片流程](images.md)。

字体配置维护于 `solution/runtime/config.ini`，默认 `fontname=Microsoft YaHei`。可指定其他可用字体，或用发布根目录内的相对 `fontpath` 私有加载字体；构建输出将配置保存为带 BOM 的 UTF-16LE。SCN 为 UTF-8，系统载体为 UTF-16LE，不做全局 GBK 字节替换或修改系统代码页。

## 当前验收边界

完整原版资源校验、215 SCN 回编再反编译、338 系统载体原文逐字节重建、23 PIMG 的结构及像素回环、运行时源码构建与补丁包重读已完成。实机已验证标题加载、独立中文正文、历史记录、存档保存及读回、多行文字速度样例。机器可读汇总见 [验证记录](validation.json)。

TJS2 菜单常量变长回填已实机验证为“文件(F)”，确认框内容仍保留原文；字节码结构校验不能替代其他实际入口的用途确认。所有正式初始输入保留日文，技术样例仅用于验证覆盖和显示链路，不表示已完成全篇翻译、全部界面图像汉化或所有系统字符串的用途判定。

## 回归命令

```powershell
python -m unittest discover -s tests -v
python -m unittest solution.decrypt.test_cx solution.script.test_system_text solution.runtime.test_build -v
```

字体 `fontpath` 非空时，把对应源文件维护在 `solution/runtime/assets/<fontpath>`；构建器只复制该显式字体到发布根下的同一路径。例如 `fontpath=chs_patch/fonts/chinese.otf` 对应源文件 `solution/runtime/assets/chs_patch/fonts/chinese.otf`。默认使用系统微软雅黑，不复制系统字体。
