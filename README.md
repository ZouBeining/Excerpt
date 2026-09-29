# excerpt

从一份 Markdown 文件中提取所有**加粗**片段（单词 / 短语 / 句子），查询词典或调用 LLM
补全释义，最终输出 Markdown 笔记与 LaTeX 讲义的完整管线。

- 单词 → 查词典（Merriam-Webster 或 Free Dictionary），失败时用 LemmInflect 还原原形再查
- 短语 / 句子 → 可选地交给 LLM 补全
- 输出：索引 JSON、`words.json`、`errors.json`、Markdown 笔记、LaTeX 文档集

---

## 一、环境准备

要求 **Python ≥ 3.12**。推荐用 [uv](https://docs.astral.sh/uv/) 管理环境：

```bash
uv sync                      # 安装运行依赖 + dev 依赖（pytest）
uv sync --no-dev             # 只装运行依赖
```

也可以直接用 pip：

```bash
python -m venv .venv
.venv/Scripts/activate       # Windows
pip install -e .             # 开发模式安装
pip install pytest           # 如需跑测试
```

安装后会注册两个命令：

| 命令 | 作用 |
| --- | --- |
| `excerpt` | 主程序：完整 5 阶段管线 |
| `excerpt-latex` | 单独把一份 `.words.json` 或 `.md` 转成 LaTeX |

> **Windows 提示**：若 `python` 报 “Python was not found”，说明命中的是 Microsoft Store
> 的占位符。请改用实际解释器路径，例如 `.venv/Scripts/python.exe`。

---

## 二、配置

复制模板并按需填写：

```bash
cp .env.example .env
```

`.env` 支持的键（**全部可选**，缺省值见 `src/common/config.py`）：

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `DICT_CHOICE` | 词典：`MW`（Merriam-Webster）或 `FD`（Free Dictionary） | `MW` |
| `DICT_API_KEY` | 词典 API Key；`MW` 必填，`FD` 不需要 | 空 |
| `OUTPUT_DIR` | 输出目录 | `.{title}_out` |
| `EXCERPT_CACHE_PATH` | 缓存文件路径 | `~/.cache/excerpt/<slug>.cache.json` |
| `COMPILE_LATEX` | 是否把 `.tex` 编译成 PDF（`True`/`False`） | `False` |
| `XELATEX` | `xelatex` 可执行文件路径（不在 PATH 时用） | 自动探测 |
| `USE_LLM` | 是否用 LLM 补全非单词条目（`True`/`False`） | `False` |
| `OPENAI_API_KEY` | OpenAI 兼容接口的 Key | 空 |
| `OPENAI_BASE_URL` | 接口地址（不要硬编码 `api.openai.com`） | 空 |
| `OPENAI_MODEL` | 模型名 | 空 |
| `TIMEOUT` / `RETRY` / `DELAY` / `PROXY` | 网络参数 | `20.0` / `3` / `0.2` / 无代理 |

**优先级：命令行参数 > `.env` > `config.py` 默认值。**
所以临时切换词典不必改文件，直接加 `--dict-choice FD` 即可。

---

## 三、快速开始

```bash
# 最简：查 MW 词典，输出到 .{title}_out（MW 需先在 .env 配好 DICT_API_KEY）
excerpt notes.md

# 只提取、不查词典（注意：若 .env 里 USE_LLM=True，仍会调用 LLM）
excerpt notes.md --no-lookup

# 完全离线、零网络：不查词典、也不调 LLM
excerpt notes.md --no-lookup --no-llm

# 用免费词典（无需 API Key）
excerpt notes.md --dict-choice FD

# 指定输出目录与标题
excerpt notes.md -o build/handout --title mynotes

# 启用 LLM 补全短语 / 句子（.env 里 USE_LLM 未开时用它临时打开）
excerpt notes.md --use-llm

# 只要 Markdown，不要 LaTeX
excerpt notes.md --no-latex

# 生成 .tex 后顺手编译成 PDF（需先装好 MiKTeX / TeX Live）
excerpt notes.md --compile

# 忽略缓存，强制重新联网查询
excerpt notes.md --no-cache
```

> **注意**：`--no-lookup` **只跳过词典查询**，不影响 LLM 阶段。
> 如果 `.env` 里 `USE_LLM=True`，仍然会对短语 / 句子发起真实的 LLM 请求。
> 想要真正零网络，必须**同时**加 `--no-llm`。

### 命令行动词速查

| 参数 | 含义 |
| --- | --- |
| `source`（位置参数） | 输入 Markdown 文件路径，**必填** |
| `-o, --output-dir` | 输出目录（默认 `.{title}_out`，位于源文件同级） |
| `--title` | 所有输出文件的统一标题（默认取源文件名） |
| `--dict-choice` | 临时指定词典，覆盖 `.env` |
| `--use-llm` / `--no-llm` | 强制开 / 关 LLM（互斥），覆盖 `USE_LLM` |
| `--env-file` | 指定另一个 `.env` 文件 |
| `--timeout` / `--retry` / `--delay` / `--proxy` | 网络参数，覆盖 `.env` |
| `--cache-path` | 指定缓存文件位置 |
| `--no-cache` | 不使用本地缓存 |
| `--no-lemma` | 关闭 LemmInflect 原形还原 |
| `--no-lookup` | 跳过词典查询（**不影响 LLM 阶段**） |
| `--no-latex` | 不生成 LaTeX |
| `--compile` / `--no-compile` | 是否把 `.tex` 编译成 PDF（互斥），覆盖 `COMPILE_LATEX` |
| `--xelatex` | 手动指定 `xelatex` 可执行文件路径 |

### 关于 `{title}`

`{title}` 是全部输出文件共用的名字，会被规整为**纯小写字母 / 数字、无空格无特殊符号**：

- 指定了 `--title` → 用它；
- 否则取输入文件名（去扩展名）；
- 若文件名含中文等无法规整的字符 → 回退为 `excerpt`。

例如 `excerpt 精读笔记.md` → `title = excerpt`，输出 `excerpt.mw.md` 等。

---

## 四、输出说明

假设 `title = test`、词典 `MW`（slug `mw`），输出目录下会得到：

| 文件 | 由哪个阶段生成 | 内容 |
| --- | --- | --- |
| `test.index.json` | `extractor` | 提取出的加粗记录（机器可读） |
| `test.mw.index.md` | `extractor` | 同一份数据的 Markdown 索引（人读） |
| `test.mw.words.json` | `lookup` + `llm` 共同维护 | 查询成功 / 补全成功的条目 |
| `test.mw.errors.json` | `extractor` 初始化，`lookup` / `llm` 维护 | 未处理 / 失败的条目 |
| `test.mw.md` | `write_md` | 最终词汇笔记（含释义、例句、词源等） |
| `main.tex`、`preamble.excerpt.tex` | `latex` 复制 | LaTeX 主文件与导言区 |
| `entries/test.tex` | `latex` | 所有条目的 subfile |

缓存（全局副作用，不在输出目录）：

```
~/.cache/excerpt/mw.cache.json     # 存词典 API 的原始返回，便于离线复用
```

### `.errors.json` 的计数器

- `total`：错误条数
- `retriable`：可重试的错误数（查询失败 / 限流 / 响应异常）
- `dict_api_key_problems`：API Key 缺失或无效的条数

若结尾看到 `[tip] ... check DICT_API_KEY`，说明有查询因 Key 问题失败了。

### 编译 LaTeX

默认**只生成 `.tex`，不编译**。加上 `--compile` 就会自动调用 `xelatex` 编译成 PDF：

```bash
excerpt notes.md --compile                 # 生成 .tex 并编译出 main.pdf
excerpt notes.md --no-compile              # 强制不编译（覆盖 .env 的 COMPILE_LATEX）
excerpt notes.md --compile --xelatex "E:\MiKTeX\miktex\bin\x64\xelatex.exe"
```

编译会自动跑**两遍**（第二次以正确生成目录与交叉引用），使用
`-interaction=nonstopmode -halt-on-error`，因此缺字等错误不会卡住等待输入。

`xelatex` 的查找顺序：`--xelatex` 指定 → `PATH` → `.env` 的 `XELATEX` →
常见 MiKTeX / TeX Live 安装目录。**找不到 `xelatex` 不会让整个流程失败**：
`.tex` 照常生成，只在日志里提示跳过编译。

想手动编译也可以：

```bash
cd .test_out
xelatex main.tex
xelatex main.tex        # 跑两遍以正确生成目录 / 交叉引用
```

### 单独使用 LaTeX 入口

已有一份 `.words.json` 或 `.md`，想直接转 LaTeX：

```bash
excerpt-latex .test_out/test.mw.words.json
excerpt-latex .test_out/test.mw.words.json --compile    # 顺便编译出 PDF
```

---

## 五、Markdown 输入的写法

程序只关心**加粗**部分（`**...**` 或 `__...__`）：

- 加粗内容是一个**单词**（匹配 `^[A-Za-z][A-Za-z'\-]*[A-Za-z]$`，长度 ≥ 2，
  只含字母 / 连字符 / 撇号）→ 进入词典查询；
- 含标点 → 判为**句子**（`sentence`）；
- 不含标点但不是单词 → 判为**短语**（`phrase`）。

短语和句子不会查词典，只会写入 `errors.json`，若开启 LLM 则由 LLM 补全。

同一单词多次出现会按归一化键合并（`word.lower().strip("'-")`），
保留首次出现的大小写和行号。

示例：

```markdown
Tushman, the middle-school **director**.
And when she **hung up**, I was like, "**what's up**, what did he say?"
```

---

## 六、如何测试

### 1. 跑全部测试

```bash
uv run pytest                            # 推荐
pytest                                   # 已激活虚拟环境时
.venv/Scripts/python.exe -m pytest       # Windows 直接指定解释器
```

预期结果：**169 passed**。

### 2. 常用测试命令

```bash
pytest -q                                  # 精简输出
pytest -v                                  # 每个用例一行
pytest tests/test_lookup.py                # 只测某个文件
pytest tests/test_lookup.py::TestMwData     # 只测某个类
pytest -k "lemma or cache"                  # 按名字筛选
pytest --lf                                 # 只重跑上次失败的
pytest -x                                   # 首个失败即停
```

### 3. 测试设计要点

**全部离线**，不触网、不花钱：

- 网络请求用 stub session / stub response 注入，绝不真实访问词典 API；
- LLM 调用用假 client 替代，不会真的请求 OpenAI；
- `tests/conftest.py` 里有一个 autouse fixture，把配置指向临时目录并
  **显式禁用 dotenv**（`env_path=""`），所以你本机真实的 `.env`
  不会泄漏进测试、导致意外联网。

**各文件覆盖范围**：

| 文件 | 用例数 | 覆盖内容 |
| --- | --- | --- |
| `test_extractor.py` | 19 | 加粗提取、单词判定、类型分类、去重合并 |
| `test_lookup.py` | 44 | 缓存读写、HTTP 重试与分类、MW markup 清洗、MW 数据映射、写 JSON |
| `test_llm.py` | 16 | 提示词构造、schema 校验、幂等跳过已 filled 记录 |
| `test_write_md.py` | 11 | 笔记 / 索引渲染、表头统计 |
| `test_latex.py` | 49 | LaTeX 转义、条目渲染、文件布局、`xelatex` 查找与编译（含缺工具回退） |
| `test_cli.py` | 30 | 参数解析、优先级、输出目录解析、各阶段编排 |

> Windows 上 pytest 清理临时目录时可能打印 `safe-delete ... trash-failed` 警告，
> 这是系统回收站机制的限制，**不影响测试结果**，可忽略。

### 4. 端到端手动验证

`tests/test.md` 是一份现成的样例输入：

```bash
# A. 完全离线冒烟测试：不查词典也不调 LLM，验证解析与文件落盘
excerpt tests/test.md -o .scratch/smoke --no-lookup --no-llm --no-latex

# B. 只查词典、不调 LLM（需先在 .env 配好 DICT_API_KEY）
excerpt tests/test.md -o .scratch/real --no-llm

# C. 用免费词典，无需 Key
excerpt tests/test.md -o .scratch/free --dict-choice FD --no-llm

# D. 验证 LaTeX 编译链路（需已装 MiKTeX / TeX Live）
excerpt tests/test.md -o .scratch/pdf --no-lookup --no-llm --compile
```

检查 `.scratch/*/test.mw.md` 是否有释义、`test.mw.words.json` 的
`dict` 字段是否为当前 slug、`errors.json` 的计数器是否正确；
D 用例还应得到 `main.pdf`，且 `main.log` 中不含以 `!` 开头的错误行。

### 5. 新增词典时的测试

新增一个词典只需：

1. 在 `src/common/config.py` 的 `DICT_CHOICES` 加一项
   （`slug` / `api_module` / `data_module` / `display_name` / 是否需要 Key）；
2. 在 `src/lookup/` 下补 `<slug>_api.py` 与 `<slug>_data.py`。

`lookup/__main__.py` 会按名字动态解析这些模块，无需改动主流程。

---

## 七、项目结构

```
src/
  common/config.py          # 全局配置与共享 schema（无第三方依赖，避免循环导入）
  excerpt/
    cli.py                  # 5 阶段总管线
    arg.py                  # 命令行参数
    extractor.py            # 提取加粗 + 分类
    llm.py                  # LLM 补全短语 / 句子
    write_md.py             # 生成 Markdown 笔记与索引
    latex.py                # 生成 LaTeX（含 excerpt-latex 入口）
    main.tex / preamble.excerpt.tex
  lookup/                   # 独立于 excerpt 的查询模块
    __main__.py             # 编排：查原词 → 还原原形 → 复查 → 合并 → 写 JSON
    http.py                 # 带重试的请求封装（不抛异常）
    outcome.py              # 响应分类
    cache.py                # 原始结果缓存
    write_json.py           # 原子写 words.json / errors.json
    lemmatizer.py           # LemmInflect 原形还原
    mw_api.py  mw_data.py  mw_markup.py     # Merriam-Webster
    fd_api.py  fd_data.py                   # Free Dictionary
tests/                      # 169 个离线测试
docs/                       # JSON / Markdown 模板与 API 返回示例
```

管线顺序固定为：
`extractor → lookup → llm → write_md → latex`，全部由 `excerpt/cli.py` 串联。
