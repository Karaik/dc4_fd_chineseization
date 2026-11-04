# 运行时源码来源

`src/launcher.cpp`、`src/patch.cpp`、`src/xp3_plain.h` 和构建器为本项目维护源码。启动器的挂起启动、显式注入思路参考既有项目，运行时的入口、插件导出器和二进制流接口均以本作静态结果重新核对。

| 来源 | 固定版本与用途 |
| --- | --- |
| [GalgameReverse](https://github.com/YuriSizuku/GalgameReverse/blob/a189c9831091c3033429ca5890dbdfb134ef3dd8/project/krkr/src/krkr_hxv4_patch.cpp) | `a189c9831091c3033429ca5890dbdfb134ef3dd8`；参考流入口签名与补丁重定向方法，未沿用其他作品的签名校验偏移 |
| [KrkrDump](https://github.com/crskycode/KrkrDump/blob/3af2f480c0b9e1f8d985926d9f914ae26ea2bb82/KrkrDump/dllmain.cpp) | `3af2f480c0b9e1f8d985926d9f914ae26ea2bb82`；参考插件导出器的获取方法；本作实际使用核心导出器获取函数拦截 |
| [alter-MinhookStb](https://github.com/YuriSizuku/alter-MinhookStb/tree/a471fe9dfca41b5c4eaa2900220140b8cbf07d45) | `a471fe9dfca41b5c4eaa2900220140b8cbf07d45`；`src/compat/stb_minhook_v1332.h`，版本宏为 `1332`，保留完整版权声明 |
| [MinHook 与 HDE 许可证](https://github.com/TsudaKageyu/minhook/blob/565968b28583221751cc2810e09ea621745fc3a3/LICENSE.txt) | `LICENSE.minhook.txt` 保留合并头文件所使用的上游许可证原文 |
| [KiriKiri 插件接口](https://github.com/YuriSizuku/GalgameReverse/blob/a189c9831091c3033429ca5890dbdfb134ef3dd8/project/krkr/src/compat/tp_stub.h) | `src/tvp_abi.h` 仅整理本项目使用的接口声明与调用约定；版权归 W.Dee 及贡献者 |
| [KiriKiri Z 许可证](https://github.com/krkrz/krkrz/blob/fd5c4baa6a2ef5978db1bd043634351f48667daf/LICENSE) | 保留上游原文于 `LICENSE.krkrz.txt` |

`LICENSE.minhook.txt` 保留 MinHook 与 HDE 的许可证，发布生成的运行时二进制时应随包保留本文件和两份许可证。编译产物不进入源码仓库。

图片回写工具 `solution/patch/native_images.py` 与 `solution/patch/src/NativeImages.cs` 为本项目维护源码，运行时调用本地 FreeMote 4.3.0 的 `TlgLib.dll`。该依赖不随源码分发，固定版本、来源与许可见 [图片工具说明](../../docs/images.md)。根目录 MIT 许可证仅适用于本项目自有代码，第三方文件继续遵守其保留的原许可证，游戏资源不在授权范围内。
