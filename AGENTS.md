# AGENTS.md

你的任务是开发一个 Python 程序, 用于查询一份 Markdown 文件中的加粗词并最终输出为 LaTeX 文件.

`.words.json`, `.errors.json`, `.md`, `.index.json`, `.index.md` 的模板文件在 docs/ 目录下.

docs/ 目录下还有一份 MW API 的返回示例，供你参考。

## 输入

- 命令行位置参数: 输入一个 Markdown 文件相对路径.
- 命令行选项参数: 已经在 arg.py 中定义.
- .env: 参照 .env.example.
- common/config.py: 定义了一些默认值.

其中优先级: 命令行参数 > .env > config.py.

`excerpt/cli.py` 解析命令行后, 调用 `common.config.configure(dict_choice=..., use_llm=...)`. 其他模块通过 `config.get_dict_choice()` / `config.get_dict_config()` 获取当前配置, 而不是在导入时读取常量.

## 处理和输出

### 符号约定

这些符号约定 **仅在这份 AGENTS.md** 中有效, 代码中的符号可能与之不同.

- `{title}`: 一个字符串, 所有输出文件的统一标题, 如果在命令行参数中指定则采用指定标题, 如果未指定则采用输入 Markdown 文件的标题. 这个变量必须被转换为全英文小写字母、无空格、无特殊字符的形式.

- `{dict_slug}`: 当前选择的词典的 slug. `common/config.py` 中的 `DICT_CHOICES` 定义了所有合法选项, 运行时由 `common/config.py` 从命令行、`.env` 的 `DICT_CHOICE` 或默认值中确定, 未来可能扩展. `DICT_CHOICES` 中的键为大写（如 MW）, 但所有文件名、目录名、模块名中一律使用小写 `dict_slug`（如 mw）.

- `{out_dir}`: 一个字符串, 输出文件夹名. 如果在命令行参数中指定则采用指定文件夹名, 如果未指定则采用默认值 `.{title}_out`. 除了特别说明的, 下文要求的所有输出文件都必须输出在这个文件夹下.

### 处理步骤和输出

- `excerpt/cli.py`: 总管线, 下面的所有子模块最终都汇总到 `cli.py` 中调用. 不允许对它做大的修改. 其中不应该包含任何与词典选择判断有关的逻辑——这些判断统一由 `common/config.py` 提供, 子模块通过 `config.get_dict_config()` 获取当前词典配置, 不得硬编码合法词典选项.
  - `common/config.py`: 用于配置需要全局调用的变量和函数. 模块 `excerpt` 和 `lookup` 中的程序都需要调用这个文件, 我们出于避免循环引用的考虑把它放在这里.
  - 调用顺序.
    1. `excerpt/extractor`
    2. `lookup` (查询 + 写 `.words.json` / `.errors.json`)
    3. `llm`   (补全 + 更新 `.words.json` / `.errors.json`)
    4. `excerpt/write_md` (从最终 `.words.json` 生成 `.md`)
    5. `excerpt/latex` (从最终 `.words.json` 生成 `.tex`)

- `excerpt/extractor.py`: 将输入 Markdown 文件中的所有加粗部分（可能是单个英文单词, 可能是短语, 可能是句子）提取出来.
  - 输出: 输出为 `{title}.index.json` 和 `{title}.{dict_slug}.index.md`. 两者表达同一份提取数据: .index.json 是标准 JSON, .index.md 是同一数据的 Markdown human-readable 索引.
  - 单词检验: 识别加粗记录是否为单个单词, 若是, 则加入待查询列表并传给 `lookup`；若不是, 则写入 `{title}.{dict_slug}.errors.json`, 供 `llm` 补全.
  - 单个单词判定规则.
    - 去除首尾空白后, 必须匹配正则 `^[A-Za-z][A-Za-z'\-]*[A-Za-z]$`, 且长度（含撇号、连字符）≥ 2；
    - 至少包含一个英文字母；
    - 内部只允许英文字母、连字符、撇号, 不允许数字、空白、其他标点；
    - 归一化键为 `word.lower().strip("'-")`；
    - 归一化后重复的记录合并, 保留首次出现的原始大小写和所在行号.
  - 若加粗部分包含标点符号, 则判定为句子, `type = sentence`；若不包含且不是单个单词, 则判定为短语, `type = phrase`.
  - 已有 `extractor.py`, 必须保持骨架不变, 可以少量修改.

- `lookup` 模块: 调用缓存和某个词典 API 查询加粗的单个单词. 这里涉及到多个模块, 你应当把这一步封装为一个独立于 `excerpt` 的名为 `lookup` 的新模块, 并在 `excerpt/cli.py` 中调用它. 你需要在这个新模块中编写一个 .py 程序（可以是 `__main__.py`）作为这一新模块的总入口, 并在其中调用子模块 `{dict_slug}_api.py`, `lemmatizer.py`, `write_json.py`. 对这一模块的具体要求见下文.

- `llm` 模块: 用 LLM 补全不是单个单词的 entries. 读取 `{title}.{dict_slug}.errors.json`, 选取 需要补全的记录让 LLM 补全（判定标准见下）. 已 `filled` 的记录跳过, 保证幂等. 补全结构直接就地写入 `{title}.{dict_slug}.words.json`, 并同步更新 `{title}.{dict_slug}.errors.json` 的记录（将 `status` 字段由 `pending` 改为 `filled`）. 这里涉及到多个模块, 与 `lookup` 一样, 封装为一个独立于 `excerpt` 的顶层模块, 并在 `excerpt/cli.py` 中以 `import llm` + 属性访问（`llm.run(...)`）的方式调用它. 注意事项.
  - LLM 调用统一使用 `config.get_openai_settings()` 返回的 `(api_key, base_url, model)`, 走 OpenAI 兼容的 Chat Completions 接口；如果所用 provider 支持 JSON schema, 则启用结构化输出, 否则在 prompt 中要求返回严格 JSON 并对结果做 schema 校验和失败重试. 不要硬编码 `api.openai.com`.
  - `llm/__main__.py` 只处理 `status == "pending"` 且 `type != "word"` 的记录, 也就是 `source == "extractor"` 产生的非单词记录. 单词查询失败仍由 `lookup/lemmatizer` 负责, 不由 LLM 处理.
  - 这里显然不需要 agent loop, 一条记录问一遍即可.
  - 如果输入的是一个短语, 则分析的重点应该在短语的意思、使用场景和近义/反义表达.
  - 如果输入的是一个完整的句子, 则分析的侧重点应该在**句型** —— 它传达什么、什么时候用、可以复用的语法是什么, 而不是句意, 更**不是句中某个单词的词义**. 句子记录的 `pos` 一律为字面量 `sentence`.
  - **不要** 生成词源、首次使用时间等元数据字段；这些字段在标准 schema 中可以为空.
  - 要求全英文输出.
  - 例句要求: 约两句, 每句都是完整的、受过教育的成年人会写出的自然句子, 复用该短语或句型, **不得**是初中课本水平的简单句；`synonyms` / `antonyms` 也应是地道的短语表达, 而不是孤立的扁平单词.
  - 模块结构（与 `lookup` 同构）: `llm/__init__.py` 只再导出主入口；`llm/__main__.py` 是编排（提示词、schema、请求循环、熔断, 以及 `run` / `complete_entries` / `SYSTEM_PROMPT` / `schema_for` 四个公开入口）；`llm/cache.py` 是补全缓存；`llm/check.py` 是预检探针；`llm/format.py` 是 `response_format` 方言候选链；`llm/reasoning.py` 是思考抑制字段候选链. 子模块之间不得形成循环引用, 且 `lookup` 不得 import `llm`.
  - 包的导入形态: `excerpt/cli.py` 必须写成 `import llm` 并在调用点做属性访问 `llm.run(...)`, **不得**写成 `from llm import run` —— 否则测试里 `monkeypatch.setattr("llm.run", ...)` 打不到 CLI 持有的绑定, CLI 测试会真的发出 LLM 请求.

- `excerpt/write_md.py`:
  - 根据 `{title}.index.json` 和 `{title}.{dict_slug}.words.json` 的内容稍加排版, 写入 `{title}.{dict_slug}.md`.
  - 根据 `{title}.index.json` 和 `{title}.{dict_slug}.words.json` 的内容稍加排版, 写入 `{title}.{dict_slug}.index.md`.

- `excerpt/latex.py`: 基于 `lookup` 模块生成的数据文件, 输出 LaTeX 文档.
  - 处理 LaTeX 特有的特殊符号转义和表格等问题.
  - 将 `preamble.excerpt.tex` 和 `main.tex` 复制到指定输出目录中, 并将清洗好的数据按导言区格式要求写入 .tex 文件. `main.tex` 已经使用了 `subfiles` 宏包, 程序应当在输出目录下新建 `entries` 目录, 在 `entries` 目录中写入 `{title}.tex` 文件. `main.tex` 中已存在占位符 `\subfile{entries/__ENTRIES__}`, `latex.py` 应将 `__ENTRIES__` 替换为 `{title}`（不含扩展名）, 或直接生成 `main.tex` 时写入 `\subfile{entries/{title}}`.
  - 使用 xelatex 编译, 使用 Unicode, 所有需要用到的 LaTeX 命令都已经在 preamble 文件中定义.
  - 额外要求: 在 pyproject.toml 中注册 [project.scripts], 例如: `excerpt-latex = "excerpt.latex:main"`. 该入口接收一个标准 .words.json 或 {title}.{dict_slug}.md 文件, 输出 LaTeX 文件.

### `lookup` 模块的具体要求

这个模块用于处理词典 API 调用、数据清洗、数据缓存的写入和读取、相关文件生成, 是 **独立于 `excerpt`** 的新模块.

#### 这个模块应当包含的程序和它们的功能

- 一个主程序（可以是 `__main__.py`）, 作为模块入口, 并调用 `{dict_slug}_api.py`, `write_json.py` 和 `lemmatizer.py`, 将子模块的功能整合为标准化的接口暴露给 `excerpt` 模块.

- `{dict_slug}_api.py`: 用于完成所有和网络、缓存和数据获取相关的功能, 它需要调用 `cache.py`, `{dict_slug}_data.py`.
  - 网络相关: 包括但不限于配置代理, 构造 Session, 获取 response, 处理网络异常, 等等. 如果网络异常, 则自动重试；若网络一直异常, 则传入下一步操作. 这一部分由该 Python 文件本体自己实现. 不管网络是否能连接、单词有没有查询到, 都将对应的信息传入下一步操作.
  - 缓存相关: 完成缓存的读写. 调用 `cache.py` 完成.
  - 数据清洗相关: 清洗数据. 调用 `{dict_slug}_data.py` 完成数据清洗工作.

- `cache.py` : 用于定义缓存的读写, 主要是定义了 `WordCache` 类, 不涉及具体的操作. 不同的词典 API 的缓存文件名称应该不同, 缓存文件路径一律为 `~/.cache/excerpt/{dict_slug}.cache.json`, 缓存文件中存储的应该是词典 API 返回的**原始数据**.

- `{dict_slug}_data.py`: 用于完成数据的清洗. 将 API 返回的数据清洗为标准 JSON 格式. API 返回的 JSON 数据的字段可能是动态变化的, 需要将其处理为字段不变（但可以为空）的对于所有词典 API 的返回都一样的标准格式, 便于下一步处理.

- `lemmatizer.py`: 对 `type=word` 且需要还原的词调用 LemmInflect 获取 lemma, 若 lemma 与原词不同, 则返回给 `__main__.py` 记录. 注意事项.
  - 仅对 `type == "word"` 的条目 lemmatize；
  - 仅当原词查询失败（`code != CODE_OK`）时, 才用 LemmInflect 求 lemma；
  - lemma 与原词相同则跳过；
  - lemma 查询成功则交给 `write_json.py`, 作为 `is_lemma=true、lemma_from=原词` 的独立条目追加在原词条目后面；
  - lemma 查询失败则交给 `write_json.py`, 写入 `.errors.json`, 不再递归 lemmatize.

- `write_json.py`: 接收上一步处理好的标准化数据, 对每个单词逐一判断并更新状态.
  - 若返回正常、释义可以正常解析, 则将标准化数据写入 `{title}.{dict_slug}.words.json`
  - 若返回错误或释义为空, 则读取 `{title}.{dict_slug}.errors.json`, 更新状态、合并重复, 按 word + type 去重, 再原子写入.
    - 成功时, 如果 `.errors.json` 中存在同 word + type 且 `status=pending` 的记录, 将其 `status` 改为 `filled`；
    - 失败时, 按 word + type 查找已有记录, 存在则更新 `code`、`reason`、`time`, 不存在则新增；
    - 每次写入前重算顶层 `total`、`retriable`、`dict_api_key_problems`；
    - 写入方式为原子写入（先写临时文件, `os.replace`）.
  - 所有写入 `.words.json` 的条目, `dict` 一律为当前 `dict_slug`, 无论 `source` 是 `cache`、`api` 还是 `llm`.
  - `write_json.py` 只写 `.words.json` 和 `.errors.json`；`lookup/__main__.py` 只调包内的 `write_json.py`, 不写 `.md`.

#### 架构图

```txt
lookup/__main__.py
  -> 调 {dict_slug}_api.py 查询原词
  -> 调 lemmatizer.py 获取原形
  -> 若原形不同, 再调 {dict_slug}_api.py 查询原形
  -> 合并结果
  -> 调 write_json.py 一次性写 words.json / errors.json

{dict_slug}_api.py
  -> cache.py
  -> {dict_slug}_data.py
  -> 返回标准化 entries, 不直接写最终输出
.
lemmatizer.py
  -> 负责用 LemmInflect 查询原形, 若原形与原单词不同则返回 {dict_slug}_api.py 查询原形.

write_json.py
  -> 负责写 .words.json / .errors.json. write_json.py 只负责把已合并好的标准数据落到 {out_dir}, 不再回调 {dict_slug}_api.py 或 lemmatizer.py.
```

#### 总结一下 `lookup` 的输出要求

- `{out_dir}` 下输出: `{title}.{dict_slug}.words.json`、`{title}.{dict_slug}.errors.json`.

- 缓存是全局副作用, 固定写到 `~/.cache/excerpt/{dict_slug}.cache.json`, 不属于 `{out_dir}`.

### 最终输出

`{out_dir}` 下输出.

- `{title}.index.json`（excerpt/extractor 生成）
- `{title}.{dict_slug}.index.md`（excerpt/extractor 生成）
- `{title}.{dict_slug}.words.json`（lookup/write_json 与 llm 共同维护）
- `{title}.{dict_slug}.errors.json`（excerpt/extractor 初始化, lookup/write_json 与 llm 维护）
- `{title}.{dict_slug}.md`（excerpt/write_md 生成）
- `{title}.tex`（excerpt/latex 生成, 放在 `entries/` 子目录下）
- `main.tex`、`preamble.excerpt.tex`（excerpt/latex 复制）

`~/.cache/excerpt` 下输出.

- `{dict_slug}.cache.json`

### 模板说明

#### template.words.json

- 所有 `.words.json` 条目的 `entry` 字段必须与 `config.empty_entry()` 的键完全一致, 缺的键补空值.

#### template.errors.json

- `total` 为 errors 长度.
- `retriable`、`dict_api_key_problems` 由 `lookup/write_json.py` 按 `status code` 重算, 不由手工维护.
  - `retriable`: ``code` 属于 `CODE_LOOKUP_FAILED`,`CODE_RATE_LIMITED`,`CODE_BAD_RESPONSE` 中任意一个的条目数.
  - `dict_api_key_problems`: `code` 属于 `CODE_MISSING_API_KEY`, `CODE_INVALID_API_KEY` 中任意一个的条目数.
- `id` 为 `uuid.uuid4().hex`, 每条错误记录唯一, 新增时生成, 更新时保留原值.
- `time` 使用 `datetime.now().astimezone().isoformat(timespec="seconds")` 获取.
- `type`, `source`, `reason`, `status` 均使用 `common/config.py` 定义的常量.

#### template.md

- `valid_entries: A / B` 中.
  - `A` = `.words.json` 中 `code == CODE_OK` 的条目总数（含 lemma 条目）；
  - `B` = `index.json` 中去重后加粗记录数（不含 lemma 条目）.
- `dict` 的值从 `config.get_dict_config()["display_name"]`获取.
- `lemmatized` 条: 数字为 `is_lemma == true` 的条目数, 格式为 原词 → lemma, 多个用 `,` 分隔.
- `cache_hit` 为 `.words.json` 顶层词典中 `source == "cache"` 的条目数. `source` 只在“本次运行”内有效；本次查询命中缓存则 `source=cache`, 否则 `source=api`. 每次运行都重算, 不跨运行继承.

## 开发要求

### 开发工具

- **只允许从系统里的 Git Bash 使用命令行**, 禁止使用你自带的 Git Bash 或系统里的 PowerShell.
- **只允许从系统里的 Python 和 uv 完成开发**, 禁止使用你自带的 Python 和 pip.

### 发版和 git commit

- 自己处理 `pyproject.toml` 和 `uv.lock` 的相关问题. 不允许乱加版本号, 现在的初始版本好是 `0.1.0`, 则你的版本应该从 `0.1.1` 而不是 `0.2.0` 开始. 完成初始功能并通过 `pytest` 后发 `0.1.1`；之后每个影响功能的 bug 修复发 patch 版本.

- 按照 `git-commit-skill` 的要求, 进行原子化的 git commit.

### 测试

- 使用 `pytest` 完成测试.
- 网络 API 和 LLM 调用在测试中必须 mock, 不得真实请求.
- 正式测试脚本放在 tests/ 目录下, 临时测试脚本放在 .scratch/ 目录下. 我已经在 tests/ 目录下放了一个 `test.md` 用作测试文本.

## 其他要求

- 虽然 `config.py` 指定了两个合法词典选项, 但未来可能扩展, 因此程序必须具有良好的可拓展性, 即任何地方（除了 `config.py`）都不得硬编码合法词典选项, 而是通过 config.get_dict_config(dict_choice) 取值.
- **绝对禁止** 模块与模块之间的循环引用.
- 需要全局调用（多个模块需要引入）而又不是特别复杂的变量定义或类定义或函数, 必须写在 `config.py` 中.
- **从数据进入 `write_json.py` 开始, 所有的数据都必须是标准化的（即对所有词典 API 的返回都一样, 如无可以为空）.**
- 一律用 UTF-8 格式读取和保存文件.

## LLM 阶段的并发与容错约束

- **worker 线程不得落盘.** `ThreadPoolExecutor` 内只允许"发请求 + 解析回复", 任何文件写入必须回到主线程、在所有请求结束后一次性执行. 原因: `lookup/write_json.py` 的 `write_documents` 是"读 → 合并 → 原子写", 并发调用会因读—改—写竞态互相覆盖.
- **`--llm-workers` 默认必须为 1（串行）.** 只有用户显式设置 >1 才并发. `workers <= 1` 必须走原串行路径, 保证与历史行为逐字一致.
- **LLM 的超时与重试使用独立配置键**（`llm_timeout` / `llm_retries` / `llm_backoff`）, 不得复用 `TIMEOUT` / `RETRY` / `DELAY`. 词典的 GET 与推理模型的补全, 时间量级和失败模式完全不同, 共用会让正常的长首字节延迟被误判为超时.
- **失败记录保持 `pending`.** LLM 失败时只写 `entry.detail`, **不得**改动 `entry.code` 或 `entry.source`. 这是幂等与"熔断后重跑续上"的基础: 只有 `code != CODE_OK` 的记录才会进 `.errors.json` 并保持 `pending`.
- **熔断状态不落盘.** 它描述的是单次运行, 写盘会让下次运行被一个过期的"已中止"标记误伤. 这与 `llm/check.py` 从不缓存 transient 结论是同一个理由.
- **新增 LLM provider 的思考强度写法时, 只在 `llm/reasoning.py` 的 `REASONING_CANDIDATES` 追加候选**, 不得在 `llm/__main__.py` 中硬编码 provider 名称或做供应商判断.
- **新增任何 LLM 配置项时, 必须同步四处**: `config.py` 的 `_session_settings` 默认值、`configure()` 的三级优先级赋值、`.env.example` 的键、`tests/conftest.py` 的 `delenv` 列表. **缺一不可** —— `_session_settings` 是模块级可变对象, 测试环境变量清不掉它, 漏了最后一步会导致测试之间串状态.

## LLM 结构化输出与缓存的约束

- **结构化输出必须逐级降级, 不得因供应商不支持而报错退出.** 候选列表定义在 `llm/format.py` 的 `SCHEMA_CANDIDATES`, 最后一项是"不发 `response_format`"的终结兜底 —— 它不可能再因格式被拒, 因此"供应商不支持结构化输出"在结构上就无法让该阶段失败. 全部候选被拒时只提示一次并继续.
- **`is_format_rejection` 必须以"错误里点到格式字段名"为必要条件, 不得依赖拒绝动词的具体措辞.** 端点写法各不相同（`does not support feature: structured-outputs`, `unsupported_response_format`, `json_object is not supported` …）, 把判定绑死在任何一种动词或分隔符上都会漏判. 曾因 marker 表里只有 `structured output`（空格）与 `structured_output`（下划线）而漏掉真实返回的 `structured-outputs`（连字符 + 复数）, 导致降级链完全不生效、退化成"重试同样请求再失败". 字段名用 `_FORMAT_FIELD_PATTERN` 正则匹配, 覆盖连字符 / 下划线 / 空格与单复数.
- **未归因的 400/422 也必须前进一格候选.** `_classify_llm_error` 对未被 `auth`/`rate_limit`/`server`/`network`/`bad_reply` 认领的 400/422 返回 `"bad_request"`, `_request_completion` 据此 `state.advance()`. 理由: `response_format` 是本模块唯一在变的请求字段, 换一个试总比重发一个已知被拒的请求划算. 这条兜底保证措辞超出已知列表时阶段仍能降级而不 abort. 只在 `advance()` 为真时 `continue`, 末位落回 `attempt += 1`, 终止性不变.
- **`is_format_rejection` 与 `is_reasoning_rejection` 不得互相认领对方的错误.** 两个轴对应两个不同的候选列表, 走错一个代价是每个候选多一次请求. 主保证是 `llm.__main__._request_completion` 里 reasoning 检查排在格式检查**之前**（不得调换）; 次保证是 `llm.format._REASONING_AXIS_PATTERN` —— 若拒绝动词指向 `reasoning`/`thinking`/`chat_template_kwargs`/`reasoning_effort`, 格式谓词主动让位. 格式谓词**刻意不匹配** `invalid_request_error` 与裸的 `unknown parameter`, 因为那两个措辞属于 reasoning 轴.
- **降级不消耗重试预算.** 被拒的是格式字段, 不是这次请求. `state.advance()` 返回 `True` 时应 `continue` 而不 `attempt += 1`, 否则一次探测就吃掉整条记录的重试次数.
- **探测结论在进程内共享.** `complete_entries` 必须把同一个 `SchemaState` 传给所有 worker, 使第一个记录探出的写法被后续记录直接沿用. `_WORKING_INDEX` 只在请求**真正成功**后由 `confirm()` 写入 —— 越过一个候选只证明它被拒, 不等于下一个可用.
- **新增 response_format 写法时, 只在 `llm.format.SCHEMA_CANDIDATES` 追加候选**, 不得在 `llm/__main__.py` 中硬编码 dialect 判断.
- **`ReasoningState` 与 `SchemaState` 的每次变更都必须持 `_PROBE_LOCK`.** 二者都被 `complete_entries` 的多个 worker 共享, 无锁计数会让两个线程对"当前候选"产生分歧.
- **LLM 输出缓存只存已通过 `_validate` 的 payload, 永不存失败.** 超时 / 429 / 5xx / 坏 JSON 一旦落盘, 一次临时故障就被永久冻结. 缓存的值是 payload 而非成品 entry —— entry 还挂着 `code`、行号等本次运行的信息.
- **缓存键为 `sha256(词条文本 | 类型 | PROMPT_VERSION)`.** 模型名不进键（它是文件名）, 但**改动提示词或期望返回结构时必须自增 `llm.format.PROMPT_VERSION`**, 否则旧答案会被继续复用.
- **缓存文件名为 `~/.cache/excerpt/{model}.cache.json`, 模型名中的非法字符（尤其 `/` 和 `:`）必须替换为 `_`.** Windows 上 `:` 会被当作数据流分隔符, 直接写入会失败.
- **缓存默认开启的边界在 CLI 层.** `llm.run()` / `complete_entries()` 只在显式收到 `cache` 参数时才启用; 不传即不缓存, 库层不得自行打开 `~/.cache`（避免对库调用产生隐式文件副作用）.
- **写入缓存、打印日志、改动 entry 三者都只允许在主线程 (`_apply_result`) 完成.** worker 只负责"发请求 + 解析", 与"worker 线程不得落盘"是同一条约束.
- **输出必须逐条即时打印, 且保持输入顺序.** 串行路径每条 settle 后立刻打印; 并发路径用 `as_completed` 收结果, 但要用 `ready` 缓冲把过快的条目压住, 直到它前面所有位置都已打印. 不得改成"全部结束后再一次性输出".
- **缓存命中时日志显示 `ok (cache)`**, 使"这条是复用的、没有发请求"在命令行上可观测.
- **新增涉及缓存目录的测试时必须确认 `EXCERPT_CACHE_DIR` 已被指向临时目录**（`tests/conftest.py` 的 autouse fixture 负责）, 否则测试会污染开发者真实的 `~/.cache/excerpt/`.

