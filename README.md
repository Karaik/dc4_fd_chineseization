# dc4_fd_chineseization

《D.C.4 - Fortunate Departures -》全年龄 Windows 版的汉化技术工具，项目已归档。

保留 Cx 解密、XP3 解包与封包、SCN 和系统文本导出回填、PNG/TLG/CUR/PIMG 图片处理，以及运行时补丁的源码构建与验证。

当前版本不包含游戏本体、解出的文本、译文或生成的补丁。使用时自行准备同版本游戏，放入 `game/`；文本在本地生成，`game_script/` 不再纳入版本控制。

## 使用

需要 Windows、Python 3.10 及以上、MSVC x86 构建工具、Windows SDK、.NET Framework 4.8，以及项目本地的 FreeMote 4.3.0。

```powershell
python -m pip install -r requirements.txt
python main_key.py --check
python main_unpack.py --verify-cache --decompile
python main_scn_text.py export solution/unpack/work/data/scn_json
python main_system_text.py export
python main_build.py --dry-run
python main_build.py
```

构建说明见 [docs/build.md](docs/build.md)，素材回收见 [docs/handoff.md](docs/handoff.md)，技术验证记录见 [docs/validation.json](docs/validation.json) 和 [docs/handoff_validation.json](docs/handoff_validation.json)。

自有代码采用 [MIT License](LICENSE)。第三方代码保留各自许可证和[来源说明](solution/runtime/THIRD_PARTY.md)；游戏资源不属于本仓库许可证的授权范围。
