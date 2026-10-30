# 解密阶段

本作使用 Cxdec 负载过滤。参数与控制表从本项目 `game/DC4_FD.exe` 内嵌的保护模块提取，维护流程不依赖其他游戏或 GARbro 数据库。

## 文件

| 文件 | 用途 |
| --- | --- |
| `key.py` | 查找原版 EXE 内的压缩保护模块并解析参数记录 |
| `key.json` | 本作参数、原版来源摘要和提取位置 |
| `control_block.bin` | 原模块 `TABLE` 记录的 4096 字节原始控制表 |
| `cx.py` | 项目内 Cx 解密与对称加密实现 |
| `verify.py` | 完整代表样本的校验、结构检查与媒体解码 |
| `test_cx.py` | 黄金样本、分段边界、原生边界行为及来源校验测试 |

## 用法

在项目根目录重新提取密钥材料：

```powershell
python .\main_key.py
```

从原 EXE 重新提取并只读比较现有密钥材料：

```powershell
python .\main_key.py --check
```

提取器仅支持已核对摘要的本作原版 EXE。原版更换后会明确失败，须先重新确认保护模块，不能沿用旧版本结论。

解密及提取只需 Python 3.10 或更高版本的标准库。完整代表样本验证另外需要 Pillow 与可从 `PATH` 找到的 FFmpeg：

```powershell
python -m unittest solution.decrypt.test_cx
python -m solution.decrypt.verify
```

验证报告默认写入 `tmp/decrypt/verification.json`。只为媒体解码落地两段音频，保存在 `tmp/decrypt/samples/`，原版资源保持只读。

## 接口

```python
from solution.decrypt.cx import load_scheme

scheme = load_scheme()
plaintext = scheme.decrypt(segment_data, file_hash, offset=0)
ciphertext = scheme.encrypt(plaintext, file_hash, offset=0)
```

先解开 XP3 分段的 zlib 压缩，再传入本接口。`file_hash` 为索引 `adlr` 字段；`offset` 为该段在条目解压后内容中的累计偏移，不是封包物理偏移。

本接口只处理 Cx 层。TJS 文本封装、编译字节码和 PSB 由后续对应阶段解析；不得在解密接口中替换文本或修改长度。

## 来源与验证

通用 Cx 算法参考保留上游许可：

- [GARbro 的 Cx 实现](https://github.com/crskycode/GARbro/blob/ed4ff6de9ae80d6eab0c37a08671c91fa1855240/ArcFormats/KiriKiri/KiriKiriCx.cs)
- [Python 参考实现的 Cx 部分](https://github.com/YuriSizuku/GalgameReverse/blob/a189c9831091c3033429ca5890dbdfb134ef3dd8/project/krkr/src/krkr_hxcrypt.py)

本作参数来源与结构见 `docs/decryption.md`，代表样本结果见 `docs/decryption_validation.json`。样本通过不等于全部资源、脚本回环或游戏加载已通过。
