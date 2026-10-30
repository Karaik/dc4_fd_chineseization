# PIMG 图片导出与重封

## 输入与工具

本作 UI 图片位于解密后的 `solution/unpack/work/data/uipsd/`，共 23 个 PIMG。每个文件是 PSB v3，内部共 448 个 TLG5 资源、1,029 个图层。先执行本项目解密解包入口，再执行图片工具；图片工具不修改 `game/` 原版输入。

依赖 Python 3.10 以上、Pillow，以及项目本地的 `tools/Ulysses-FreeMoteToolkit-v4.3.0/`。工具来自 [FreeMote v4.3.0](https://github.com/UlyssesWu/FreeMote/releases/tag/v4.3.0)，固定源码提交为 [`5dbb2fa50919949e54d765f6d9bb4edc50bd533a`](https://github.com/UlyssesWu/FreeMote/tree/5dbb2fa50919949e54d765f6d9bb4edc50bd533a)。上游许可证位于工具目录的 `FreeMote.LICENSE.txt`，不得把本地工具缓存和生成的 PIMG 提交到源码仓库。

以下命令均以本作项目根目录为基准。

## 导出与编辑

```powershell
# 全部导出；也可在命令后只列出需要的归档路径。
python .\main_pimg.py export
python .\main_pimg.py export uipsd/title.pimg uipsd/window.pimg
```

导出结果保留归档路径，例如：

```text
solution/patch/work/images/uipsd/window.pimg/
  window.json
  window.resx.json
  window/
    613.png
    614.png
    ...
  .pimg_asset.json
```

编辑对应 PNG，保留透明通道。`window.json` 保留画布、图层数组及资源引用；`window.resx.json` 描述版本、类型、平台及资源文件路径；`.pimg_asset.json` 记录原始输入哈希与语义基线，用于识别真正的图片修改。

默认再次导出只保留已有编辑目录，不回写 JSON、PNG 或基线。输入原文件变化或目录缺少有效基线时会停止。明确需要重新导出时使用 `--force`：

```powershell
python .\main_pimg.py export uipsd/window.pimg --force
```

覆盖前把完整已有目录复制到独立的 `images_backup_<编号>/`。新导出先在独立目录完成验证，再复制到编辑目录；不执行删除操作，编辑目录里的附加用户文件也会保留。

## 重封与验证

```powershell
python .\main_pimg.py build uipsd/window.pimg
python .\main_pimg.py build
```

未指定输出时使用新的 `solution/patch/work/images_build_<编号>/`，也可用 `--output` 指定一个空的工作目录。编译输出与编辑输入必须分离。

每项重封都重新调用 `PsbDecompile` 解包，并比较：

- 画布及完整图层数组的顺序、坐标、尺寸、透明度、可见性、分组和 `same_image`。
- 逻辑资源键与引用共享关系。
- 每张内部图片的宽高及全部 RGBA 像素。
- 除导出资源路径外的 FreeMote 元数据。

验证结果写入 `.build_report.json`。二进制哈希仅作为产物记录，不作为图片等价的证明。

真实输出表明，FreeMote 会重新排列 `#resource#N` 编号，并可能给 TLG5 增加 TLG0/sds 外壳和工具标签。这些规范化可以改变 PIMG 哈希。比较会规范化编号，同时保留同一资源引用对应的 JSON 路径集合，防止错误合并或串图被忽略。

例如 `window.json` 中的 `613.tlg: #resource#5` 是内部资源引用，而 `window.resx.json` 中的 `Resources["613"]: "window/613.png"` 才是资源文件位置，不能把两种编号混用。`layers` 中 `layer_id=-1` 可重复，必须保留数组顺序；不能转换成按 ID 去重的字典。也不能按 PNG 尺寸改写图层几何，例如本作窗口 621 层宽高为零，而资源本身为 32×32。

## 显式发布

导出和重封都不会修改正式静态补丁或清单。完成编辑后，明确列出要发布的归档路径：

```powershell
python .\main_pimg.py publish uipsd/window.pimg
```

发布会先重封并完成全部语义验证，只将相对原始基线确实有变化的条目复制到 `solution/patch/base/chs_patch/<原归档路径>`，再把该路径追加到 `solution/patch/base/chs_patch_manifest.txt`。未修改的资源不会被自动装入补丁。

若目标位置已有不同成品，默认停止；显式使用 `publish ... --force` 时先备份旧成品和旧清单。不删除原版、编辑输入、用户附加文件或已有成果。

## Python 接口

实现位于 `solution.patch.images`：

```python
export_images(source, output, tools, selected=None, force=False)
build_images(edit_root, output, tools, selected=None)
publish_images(edit_root, tools, selected, force=False)
```

`selected` 使用 `uipsd/window.pimg` 这样的归档内完整路径；`None` 表示导出或重封全部，发布必须提供非空显式选择。返回值包含各项路径、验证计数、内部资源数和变化状态。工作输出限定在本项目的 `solution/patch/work/`。

```powershell
python -m unittest tests.test_pimg -v
```

测试覆盖重导出保护、强制导出备份、外部资源路径拒绝、透明度/坐标/资源引用错误检测、真实 PIMG 回编及临时 RGBA 像素修改、显式发布与未改图片排除。临时编辑和发布测试只写入独立工作目录，不把测试图片放入正式静态补丁。

## 本次验证结果

2026-10-31 执行全部 23 个原图导出与未修改回编，重解包逐项通过：448 张内部图片的 RGBA 像素和尺寸一致，1,029 个图层及全部结构字段一致。23 个回编文件的二进制哈希均与原文件不同，其中 21 项出现资源编号重排；语义变化数为零。报告位于 `solution/patch/work/pimg_roundtrip_20261031_01/.build_report.json`。

另在独立测试目录把 `indicator/3.png` 的一个像素改为 RGBA `(17,34,51,119)`，回编并重导出后该值精确保留，透明度和所有图层坐标均通过比较。该测试修改没有进入默认编辑目录或正式静态补丁。6 项自动测试全部通过；正式静态清单仍为空，没有自动发布未改图。
