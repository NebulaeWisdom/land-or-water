[中文](README.zh.md) · [English](README.md)

# 陆地还是水域?(Land or Water?)

一个 LLM 基准测试:对可配置经纬网格上的每个点,问模型该位置在陆地还是水域,
然后把答案与 GSHHG 海岸线数据库比对打分。自带 NiceGUI 工作台、CLI 运行器,
以及一个记录 token/延迟统计、可导出 CSV/JSON/HTML 的 SQLite 结果库。
灵感来自推特。

## 环境要求

- Python >= 3.11。
- 一个可访问的 OpenAI 兼容网关(基准测试通过 HTTP 调用它)。

## 安装

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy project.yaml.sample project.yaml
```

POSIX shell:`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && cp project.yaml.sample project.yaml`。

编辑 `project.yaml`,填上网关的 `base_url`、`api_key` 和 `model`。
`project.yaml` 已被 git 忽略(它带着你的 API key);提交的只有
`project.yaml.sample`。

所有命令都在仓库根目录(即存放 `project.yaml` 的目录)执行:CLI 和面板都从
当前工作目录解析 `project.yaml` 和 `assets/`。项目不以任何包的形式安装:
`land_water/` 直接从仓库根导入。

## 真值数据

用作真值的五个 GSHHG 图层以裁剪后的归档文件提交在
`assets/gshhg-land-water-2.3.7.zip`(约 71 MB),所以正常 clone 不需要额外下载;
`paths.gshhg_archive` 指向它。归档保留了完整的 `.shp`/`.shx`/`.dbf`/`.prj`
文件和上游声明文件 —— 没有简化、没有栅格近似 —— 因此真值语义不变。

从原始 GSHHG 2.3.7 发行包重新生成该归档是可选的:
把 <https://github.com/GenericMappingTools/gshhg-gmt/releases/download/2.3.7/gshhg-shp-2.3.7.zip>
下载到仓库根作为 `paths.gshhg_source_archive`(`gshhg-shp-2.3.7.zip`),然后运行

```bat
.venv\Scripts\python.exe -m tools.build_truth_archive
```

GSHHG 采用 LGPL-3.0-or-later 许可;见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) 和 `licenses/gshhg/`。

保真度:用真实的 `classify_points`,原始归档 对比 提交的裁剪归档,实测结果:

| 点集 | 点数 | 标签不一致数 |
| --- | ---: | ---: |
| 已存的 2° 网格 | 16,200 | 0 |
| 完整 1° 网格 | 64,800 | 0 |
| 多边形边界顶点 | 1,491 | 0 |

拷贝的全部 25 个上游成员逐字节相同(归档 149,157,845 B → 74,398,375 B,
49.9%),所以在分类器不变的前提下,任意网格的标签都一致,而不只是测过的那几个
点集。

## 配置(`project.yaml`)

- `paths.database` —— SQLite 结果文件(相对仓库根)。
- `paths.gshhg_archive` —— 运行时使用的已提交裁剪版 GSHHG 归档
  (`assets/gshhg-land-water-2.3.7.zip`)。
- `paths.gshhg_source_archive` —— 原始 GSHHG 2.3.7 zip,只被
  `tools/build_truth_archive.py` 用来重建 `paths.gshhg_archive`。
- `paths.exports` —— 面板/CLI 写导出的目录。
- `ui.language` / `ui.languages` —— 界面语言(`zh` 或 `en`)。
- `ui.host` / `ui.port` / `ui.refresh_seconds` —— 面板地址和实时刷新间隔。
- `ui.map.history_step_degrees` —— 为测试配置已被删除的结果集画图时用的网格大小;
  `ui.map.colors` —— 图例颜色;`ui.map.export_width` —— 导出地图图的像素宽度。
- `ui.new_test` —— 面板"新增配置"时复制的模板。
- `tests` —— 一个基准一个条目,每个包含:
  - `name` —— 唯一测试名(结果即按它归档)。
  - `base_url`、`api_key`、`model` —— 网关地址和模型 id。
  - `protocol` —— `chat`(`/chat/completions`)或 `responses`(`/responses`,
    总是流式)。
  - `response_envelope` —— 仅 `chat` 有效:`success_data` 读
    `choices[0].message.content`,`openai` 用原始响应文本。
  - `request_parameters` —— 原样发出的额外请求参数;由 `{key, type, value}`
    行组成的列表,`type` 取 `string`/`number`/`boolean`/`json`,`value` 用对应的
    YAML 标量类型。带点号的 key 会展开成嵌套对象(例如 `responses` 用
    `reasoning.effort`,`chat` 用 `reasoning_effort`)。空列表则不发送任何额外
    参数。schema 管理的字段(`model`、`messages`、`input`、`stream`、
    `max_tokens`、`max_output_tokens`、`temperature` 等)不能在这里覆盖。
  - `proxy` —— 访问网关用的 HTTP(S) 代理,`null` 表示不用代理。
  - `limits.rpm` / `limits.max_in_flight` / `limits.tpm` —— 滚动 60 秒的请求数
    和 token 上限,以及最大并发在途请求数(`tpm: null` 关闭 token 限制)。
  - `retries`、`retry_delay_seconds`、`timeout_seconds` —— 失败处理;服务器返回
    `Retry-After` 时会抬高延迟。
  - `max_output_tokens`、`temperature` —— 采样参数;`null` 表示不发送这两个字段。
  - `prompt` —— 提示词模板,用 `{latitude}` 和 `{longitude}` 格式化。
  - `step_degrees` —— 网格边长;采样点是格子中心。
  - `point_count` —— 要采样的网格点数(`null` = 整个网格)。
  - `seed` —— 可复现的点选取与执行顺序。

## 运行

面板(推荐;也可以用 `start.cmd` 快捷方式):

```bat
.venv\Scripts\python.exe -m land_water serve
```

打开 <http://127.0.0.1:8501/>。勾选要跑的配置,开始,然后看实时进度;面板同时
列出已存结果,带地图和导出。

CLI:

```bat
.venv\Scripts\python.exe -m land_water run --tests example
.venv\Scripts\python.exe -m land_water report --tests example
```

`run` 执行指定测试(省略 `--tests` 即全部);`report` 打印统计并为指定测试写导出。

## 读结果

`paths.database` 里每行对应一个网格点:`truth`(GSHHG 标签)、`answer`(模型文本)、
`status`、token 计数、延迟、尝试次数,以及原始 `request_json`/`response_json`。

- `status` —— 答案去掉首尾空白并转小写后正好是 `land` 或 `water` 时为 `ok`;
  调用成功但答案是别的内容时为 `invalid`;重试全部失败后仍出错时为 `failed`。
- `truth` —— 覆盖该点的最深 GSHHG 图层:L1 陆地,L2 湖水,L3 湖中岛陆地,
  L4 湖中岛上池塘的水域,L5(南极冰盖)陆地。
- 重跑会续跑:某个测试名下已存的点会被跳过,所以一次运行可以中断后再继续。
- 统计汇总 ok/invalid/failed 数量、`ok` 行的准确率、token 总量(网关有上报时会
  包含 reasoning/cached token 明细)以及平均延迟。面板会画出已存点的世界地图,
  并可导出 CSV、JSON 和 HTML。

## 许可

- 项目源代码:MIT,见 [`LICENSE`](LICENSE)。
- GSHHG 2.3.7 数据:LGPL-3.0-or-later;再分发或派生数据需要遵守的义务记录在
  [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md),许可全文在
  `licenses/gshhg/`。
