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

| 命令              | 作用                                   |
| --------------- | ------------------------------------ |
| `excerpt`       | 主程序：完整 5 阶段管线                        |
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

| 变量                                      | 说明                                              | 默认                                   |
| --------------------------------------- | ----------------------------------------------- | ------------------------------------ |
| `DICT_CHOICE`                           | 词典：`MW`（Merriam-Webster）或 `FD`（Free Dictionary） | `MW`                                 |
| `DICT_API_KEY`                          | 词典 API Key；`MW` 必填，`FD` 不需要                     | 空                                    |
| `OUTPUT_DIR`                            | 输出目录                                            | `.{title}_out`                       |
| `EXCERPT_CACHE_PATH`                    | **词典**缓存文件路径                                    | `~/.cache/excerpt/<slug>.cache.json` |
| `EXCERPT_CACHE_DIR`                     | **缓存目录**，覆盖词典缓存、LLM 缓存与 LLM 预检结果三类文件的落点          | `~/.cache/excerpt`                   |
| `COMPILE_LATEX`                         | 是否把 `.tex` 编译成 PDF（`True`/`False`）              | `False`                              |
| `XELATEX`                               | `xelatex` 可执行文件路径（不在 PATH 时用）                   | 自动探测                                 |
| `USE_LLM`                               | 是否用 LLM 补全非单词条目（`True`/`False`）                 | `False`                              |
| `OPENAI_API_KEY`                        | OpenAI 兼容接口的 Key                                | 空                                    |
| `OPENAI_BASE_URL`                       | 接口地址；可写 API 根地址或完整端点（末尾的 `/chat/completions` 会被自动去除） | 空                                    |
| `OPENAI_MODEL`                          | 模型名                                             | 空                                    |
| `TIMEOUT` / `RETRY` / `DELAY` / `PROXY` | 词典网络参数                                          | `20.0` / `3` / `0.2` / 无代理           |
| `LLM_TIMEOUT`                           | LLM 单次请求超时（秒）                                    | `60.0`                               |
| `LLM_RETRIES`                           | LLM 请求失败后的重试次数                                   | `3`                                  |
| `LLM_BACKOFF`                           | LLM 重试的退避基数（秒），实际等待为 `基数 × 第几次`                | `0.5`                                |
| `LLM_WORKERS`                           | LLM 并发请求数，取值 1–16；`1` 即完全串行（旧行为）                 | `1`                                  |
| `LLM_REASONING`                         | 手动指定关闭"思考"的原始 JSON，优先于内置候选列表                     | 空                                    |

> **LLM 的超时与重试独立于 `TIMEOUT` / `RETRY`。**
> 后两者只作用于词典查询（默认 20 s / 3 次），因为一个 GET 请求和一次推理模型的
> 补全，时间量级和失败模式完全不同。若共用，推理模型常见的长首字节延迟会被 20 s
> 误判为超时。

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

### LLM 预检（preflight）

只要 `USE_LLM=True`，程序在**任何处理开始之前**会先用一个最小请求探测 LLM
配置是否可用（`excerpt/llm_check.py`）：

- **配置有效** → 打印 `[llm] configuration verified: ok`，继续执行。
- **配置无效**（401 / 403 / 404 / 缺字段 / 网络不可达）→ 打印一行 `[error]`
  并安全退出（返回码 `3`），此时不会浪费词典配额。
- **配置有效但暂时不可用**（429 限流 / 5xx / 上游拒绝服务，例如地区限制）
  → 打印 `[warn]` 并**继续执行**，避免免费额度临时繁忙时整个程序无法运行。

验证通过的结果会缓存到 `~/.cache/excerpt/llm.check.json`；缓存键是
`base_url + model + API Key 的 SHA-256`（从不写入明文 Key）。只要配置没变且
之前有效，后续运行会直接复用，不再浪费请求配额。**失败与临时故障都不会写缓存**，
所以修好配置后立即生效。

用 `--no-llm-check` 可跳过预检，用 `--no-cache` 可强制重新探测。

> 探针请求同样携带"关闭思考"字段。否则推理模型会为了回一个 token 而先跑一整段
> 思维链：既慢又白烧额度，在免费档上还更容易被限流——而限流的探针按设计不写缓存，
> 于是**每次运行都要重探一次**，形成净损失。

### LLM 补全的推理强度

推理模型（`o3`、`nemotron`、`deepseek-reasoner` 等）在给出答案前会先"思考"一大段，
而此时任务只是产出六个结构化字段——思维链**没有地方存放**，token 被计费、丢弃，
并让每条记录都多等几秒。因此每次请求都会附带一个"关闭思考"的字段。

问题在于**这个字段没有标准**，各家的写法互不兼容：

| 供应商            | 写法                                                  |
| -------------- | --------------------------------------------------- |
| OpenRouter     | `reasoning: {enabled: false}`                       |
| DeepSeek / vLLM | `chat_template_kwargs: {thinking: false}`           |
| Qwen / 百炼     | `chat_template_kwargs: {enable_thinking: false}`    |
| OpenAI o 系列   | `reasoning_effort: "minimal"`                       |

程序不猜供应商，而是**按顺序逐个尝试**上表中的候选写法，用第一个被接受的，并在
进程内记住结论（**不落盘**：这个字段随供应商 API 变动，缓存一个过期的结论只会把
程序钉死在一个已被废弃的写法上）。若全部被拒，则退回**不发送任何该类字段**，改由
`max_tokens` 兜底截断，保证开销仍然可控。

如果你的供应商需要表中之外的写法，用 `LLM_REASONING` 直接指定原始 JSON 即可，
它的优先级最高：

```bash
LLM_REASONING={"reasoning": {"enabled": false}}
```

> **更省事的办法**：这个任务只需要六个字段的英文释义，**并不需要推理模型**。
> 换成一个非推理的小模型，延迟和费用都会低一个数量级，上述机制也就不再重要了。

### 结构化输出的降级

补全要求返回六个固定字段，所以程序会请求**结构化输出**（`response_format`）。
但各端点对这个字段的支持程度差别很大：有的支持完整的 JSON Schema 契约，有的接受对象
却拒绝后来才加入规范的 `strict` 键，有的只认 `json_object`，还有的干脆拒绝整个字段。
没有任何接口可以事先查询能力，只能**逐个试出来**：

| # | 候选 `response_format`                        | 说明                          |
| - | ------------------------------------------- | --------------------------- |
| 1 | `json_schema` + `strict: true`              | 现代契约：服务端按 schema 校验返回        |
| 2 | `json_schema` + `strict: false`             | 部分聚合服务只实现了旧版草案，会拒绝 `strict` |
| 3 | `json_object` 内联 schema                     | 非规范但在实际中出现过                 |
| 4 | 纯 `json_object`                             | 只保证返回是合法 JSON，字段由提示词与本地校验负责 |
| 5 | **不发 `response_format`**                    | 终结兜底：这一项不可能再因"格式"被拒          |

关键性质：

- **降级不消耗重试次数**。被拒的是格式字段，不是这次请求；换一种写法继续，重试预算不动。
- **判定结果在进程内共享**。第一条记录探出的可用写法，后面的记录直接沿用，探测成本
  只付一次，不是每条记录付一次。
- **结构化输出永不导致整批失败**。候选列表的最后一项是"什么都不发"，它不可能再因格式
  被拒，所以"供应商不支持结构化输出"永远不会让这一阶段报错退出——最差也只是退回
  提示词 + 本地校验。全部候选都被拒时只提示一次：
  `[llm] structured output unsupported; relying on the prompt and local validation`。

### LLM 的并发、超时与重试

| 行为      | 默认                                                          | 说明                                                                    |
| ------- | ----------------------------------------------------------- | --------------------------------------------------------------------- |
| 并发      | `LLM_WORKERS=1`（串行）                                         | 设为 `2`–`16` 可并发发请求，显著缩短总时长；但免费档 RPM 很低时，并发只会更快撞满限流               |
| 超时      | `LLM_TIMEOUT=60.0`                                          | 独立于词典的 `TIMEOUT`；同时关掉了 SDK 自带的 600 s 默认值与内层重试，避免与自身重试相乘              |
| 重试      | `LLM_RETRIES=3`，退避 `LLM_BACKOFF × 第几次`                       | 只有**可恢复**的错误才重试：超时、429、5xx、返回体不是合法 JSON                                  |
| 立即中止    | —                                                           | `401`/`403` 属于配置问题，重试无意义，会立刻中止整批                                        |
| 连续失败熔断  | 并发模式下连续 5 条失败                                                | 停止发新请求，剩余记录**保持 `pending`**，下次运行自动续跑（幂等）                                |

并发只在"发请求 + 解析"这一段；`.words.json` / `.errors.json` 的写入**始终留在主线程**
并在全部请求结束后一次性完成 —— 这两个文件是"读—合并—原子写"，多线程同时写会互相覆盖。

因为失败记录会被保留为 `pending`，被熔断跳过的条目**不会丢失**：修好网络或额度后
直接重跑同一条命令即可续上。

### LLM 输出缓存

一次补全是整个流程里最贵的操作：要花钱、要等，在免费档上还要占限流额度。
同一份文档重跑时，记录的文本、类型和提示词都没变，答案本来就已经知道，再问一遍纯属浪费。

因此每次补全前都会先查缓存，命中就直接复用，**一次请求都不发**：

```
  [llm 1/4] 'returning your call': ok          ← 首次运行：真实请求
  [llm 2/4] 'take for granted': ok

  [llm 1/4] 'returning your call': ok (cache)  ← 再次运行：零请求
  [llm 2/4] 'take for granted': ok (cache)
```

缓存位置 `~/.cache/excerpt/{model}.cache.json`，**一个模型一个文件** —— 同一个记录换个
模型来答是不同的答案，不是过期答案。模型名里的 `/` 和 `:` 会被替换成 `_`
（如 `vendor/model:free` → `vendor_model_free.cache.json`），因为 Windows 不允许文件名
出现 `:`。

| 项   | 规则                                                                  |
| --- | ------------------------------------------------------------------- |
| 键   | `sha256(词条文本 \| 类型 \| PROMPT_VERSION)`                               |
| 值   | **已通过 schema 校验**的 payload，不是成品 entry（entry 还挂着 `code`、行号等本次运行的信息）      |
| 失效  | 改动词条文本、类型，或**升级提示词**（`PROMPT_VERSION` 自增）即自动失效；无需迁移，也不改文件结构         |
| 不缓存 | 失败一律不落盘：超时、429、5xx、返回体不是合法 JSON —— 否则一次临时故障会被永久冻结               |

> **缓存归属**：只有命令行会启用缓存。`llm.run()` / `complete_entries()` 不传 `cache` 参数
> 就完全不碰 `~/.cache`，作为库调用时没有任何隐式文件副作用。

想强制重新联网补全，加 `--no-llm-cache`；想换缓存位置，用 `--llm-cache-path`
或环境变量 `EXCERPT_CACHE_DIR`（后者是**目录**级覆盖，同时作用于词典缓存和 LLM 预检结果）。

### 命令行动词速查

| 参数                                              | 含义                                        |
| ----------------------------------------------- | ----------------------------------------- |
| `source`（位置参数）                                  | 输入 Markdown 文件路径，**必填**                   |
| `-o, --output-dir`                              | 输出目录（默认 `.{title}_out`，位于源文件同级）           |
| `--title`                                       | 所有输出文件的统一标题（默认取源文件名）                      |
| `--dict-choice`                                 | 临时指定词典，覆盖 `.env`                          |
| `--use-llm` / `--no-llm`                        | 强制开 / 关 LLM（互斥），覆盖 `USE_LLM`              |
| `--no-llm-check`                                | 跳过 LLM 配置预检（默认会先验一次）                     |
| `--llm-timeout`                                 | LLM 单请求超时，默认 `60.0`（独立于 `--timeout`）      |
| `--llm-retries`                                 | LLM 重试次数，默认 `3`（独立于 `--retry`）              |
| `--llm-backoff`                                 | LLM 重试退避基数，默认 `0.5`                          |
| `--llm-workers`                                 | LLM 并发数，默认 `1`（串行，与旧行为一致）                   |
| `--no-llm-cache`                                | 忽略且不写 LLM 输出缓存（默认会读写 `~/.cache/excerpt/{model}.cache.json`） |
| `--llm-cache-path`                              | 指定 LLM 输出缓存文件位置                            |
| `--env-file`                                    | 指定另一个 `.env` 文件                           |
| `--timeout` / `--retry` / `--delay` / `--proxy` | 网络参数，覆盖 `.env`                            |
| `--cache-path`                                  | 指定缓存文件位置                                  |
| `--no-cache`                                    | 不使用本地缓存                                   |
| `--no-lemma`                                    | 关闭 LemmInflect 原形还原                       |
| `--no-lookup`                                   | 跳过词典查询（**不影响 LLM 阶段**）                    |
| `--no-latex`                                    | 不生成 LaTeX                                 |
| `--compile` / `--no-compile`                    | 是否把 `.tex` 编译成 PDF（互斥），覆盖 `COMPILE_LATEX` |
| `--xelatex`                                     | 手动指定 `xelatex` 可执行文件路径                    |

### 关于 `{title}`

`{title}` 是全部输出文件共用的名字，会被规整为**纯小写字母 / 数字、无空格无特殊符号**：

- 指定了 `--title` → 用它；
- 否则取输入文件名（去扩展名）；
- 若文件名含中文等无法规整的字符 → 回退为 `excerpt`。

例如 `excerpt 精读笔记.md` → `title = excerpt`，输出 `excerpt.mw.md` 等。

---

## 四、输出说明

假设 `title = test`、词典 `MW`（slug `mw`），输出目录下会得到：

| 文件                                | 由哪个阶段生成                             | 内容                     |
| --------------------------------- | ----------------------------------- | ---------------------- |
| `test.index.json`                 | `extractor`                         | 提取出的加粗记录（机器可读）         |
| `test.mw.index.md`                | `extractor`                         | 同一份数据的 Markdown 索引（人读） |
| `test.mw.words.json`              | `lookup` + `llm` 共同维护               | 查询成功 / 补全成功的条目         |
| `test.mw.errors.json`             | `extractor` 初始化，`lookup` / `llm` 维护 | 未处理 / 失败的条目            |
| `test.mw.md`                      | `write_md`                          | 最终词汇笔记（含释义、例句、词源等）     |
| `main.tex`、`preamble.excerpt.tex` | `latex` 复制                          | LaTeX 主文件与导言区          |
| `entries/test.tex`                | `latex`                             | 所有条目的 subfile          |

缓存（全局副作用，不在输出目录）：

```
~/.cache/excerpt/mw.cache.json          # 存词典 API 的原始返回，便于离线复用
~/.cache/excerpt/{model}.cache.json     # 存 LLM 补全：已通过校验的 payload，按模型分文件
~/.cache/excerpt/llm.check.json         # 存 LLM 配置预检结论
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

预期结果：**305 passed**。

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
  不会泄漏进测试、导致意外联网；它还把 `EXCERPT_CACHE_DIR` 指到临时目录，  
  因此测试**不会读写你真实的 `~/.cache/excerpt/`**。

**各文件覆盖范围**：

| 文件                     | 用例数 | 覆盖内容                                        |
| ---------------------- | --- | ------------------------------------------- |
| `test_extractor.py`    | 19  | 加粗提取、单词判定、类型分类、去重合并                         |
| `test_lookup.py`       | 44  | 缓存读写、HTTP 重试与分类、MW markup 清洗、MW 数据映射、写 JSON |
| `test_llm.py`          | 55  | 提示词构造、schema 校验、幂等跳过已 filled 记录、错误摘要、超时与 token 上限、退避重试、错误分类、熔断、并发、推理强度降级、结构化输出降级、逐条流式输出、缓存命中 / 回填 |
| `test_llm_cache.py`    | 29  | LLM 输出缓存：模型名消毒、键构造、读写往返、拷贝语义、原子写、损坏 / 版本不符容错、禁用态 |
| `test_llm_check.py`    | 27  | LLM 预检：指纹、缓存读写、HTTP 分类、端点拼接、错误解包、探针关闭思考      |
| `test_llm_format.py`   | 16  | 结构化输出候选列表：顺序、schema 注入、非破坏性、越界退化、进程内记忆、线程安全 |
| `test_llm_reasoning.py` | 10 | 思考强度候选列表：覆盖优先、逐个降级、兜底、进程内缓存、线程安全           |
| `test_write_md.py`     | 11  | 笔记 / 索引渲染、表头统计                              |
| `test_latex.py`        | 49  | LaTeX 转义、条目渲染、文件布局、`xelatex` 查找与编译（含缺工具回退）  |
| `test_cli.py`          | 45  | 参数解析、优先级、输出目录解析、各阶段编排、LLM 预检门控、LLM 配置项、LLM 缓存开关 |

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
    llm_check.py            # LLM 配置预检（用 requests，带缓存）
    llm_reasoning.py        # 关闭"思考"的候选写法（OpenRouter / DeepSeek / Qwen / o 系）
    llm.py                  # LLM 补全短语 / 句子（超时、退避重试、熔断、可选并发）
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
tests/                      # 240 个离线测试
docs/                       # JSON / Markdown 模板与 API 返回示例
```

管线顺序固定为：  
`extractor → lookup → llm → write_md → latex`，全部由 `excerpt/cli.py` 串联。
