# CGOProf：面向 cgo 边界低效的 Contract-Aware 分析与可证明优化

> 本文档同时记录当前原型、面向 CGO 2027 的研究设计和论文大纲。为避免混淆，文中使用“已实现”“部分实现”“计划实现”区分工程事实与目标贡献。

## 1. 工作概览

本工作围绕 Go 语言中的 `cgo` 跨语言调用场景，构建一个面向 Go/C 边界低效行为的跨层性能分析与优化系统，暂命名为 **CGOProf**。工具的目标不是替代通用 profiler，而是回答通用 profiler 难以直接回答的一类问题：

> 程序的性能损失是否发生在 Go 与 C 之间的语言边界上？如果是，成本由边界切换、数据物化、内存管理、安全检查还是 callback 往返造成？相关 Go/C API contract 是否允许安全地消除这些成本？

CGOProf 的目标研究路径是：

1. 扫描 Go 项目中的 cgo call site。
2. 使用 Go AST source-to-source rewriting 生成插桩后的项目副本。
3. 运行插桩项目并收集 JSONL 格式的跨层事件。
4. 推断 C API 的 ownership、lifetime、escape、callback 和 mutability contract。
5. 将一次 cgo 交互的成本分解为 boundary transition、C work、data materialization、allocation/free、pointer guard 和 callback 等成分。
6. 构建 **Ownership-Aware CGO Interaction Graph**，关联 call site、C 函数、对象、转换、内存操作、pointer check、callback 和 contract。
7. 运行三类五模式诊断规则，区分“仅观察到热点”“可能可消除”和“满足安全改写条件”的 finding。
8. 对满足 proof obligations 的 finding 生成候选 rewrite，执行测试、race check、profile 对比和 benchmark 验证。

当前原型已经实现步骤 1–3、基础版本的步骤 5–7，以及示例级优化验证；contract inference、ownership-aware object flow、严格的 graph motif 检测和通用自动 rewrite 尚属于计划工作。

五条现有 detector 与目标语义的对应关系如下：

| 上位类别 | 目标语义模式 | 当前实现名称 | 当前状态 |
|---|---|---|---|
| Control-Transfer Amplification | Forward Small-Call Amplification | `small-call-detector` | 已实现基础阈值检测；待加入边界成本分解和 batchability 条件 |
| Control-Transfer Amplification | Reentrant Callback Amplification | `callback-pingpong-detector` | 已实现 callback 聚合；待用 `Go -> C -> Go` graph motif 确认 ping-pong |
| Cross-Boundary Materialization Redundancy | Outbound Materialization Redundancy | `conversion-copy-detector` | 已实现通用转换/复制计数；需收窄为 Go-to-C，避免与 inbound 规则重叠 |
| Cross-Boundary Materialization Redundancy | Inbound Materialization Redundancy | `inbound-copy-detector` | 已实现 C-to-Go 方向统计；待加入对象身份、重复物化和消费方式分析 |
| Ownership/Contract Enforcement Overhead | Avoidable Pointer-Guard Overhead | `pointer-check-overhead-detector` | 已实现热点候选检测；待由 noescape/nocallback/lifetime contract 判断是否可消除 |

## 2. Idea 产生过程

最初的出发点来自对 DrPy 论文的理解。DrPy 关注多层 Python 应用中的跨层内存低效，尤其是不同层之间的数据冗余复制。它带来的启发是：现代应用通常由高级语言、native library、runtime 和 FFI glue code 等多层组成，性能问题可能产生于层与层的连接处，而不能只在单一语言内部解释。

沿着这个方向继续思考，会自然遇到两个选择：

1. 继续在 Python 生态中深挖新的跨层低效行为。
2. 将跨层性能分析思想迁移到其他语言和运行时系统中。

Python 生态中已经存在较多 profiler、memory profiler、native extension 分析工具和跨语言数据移动研究。相比之下，Go 的 cgo 场景有几个很适合开展工作的特点：

- Go 大量使用 C library 封装，例如 SQLite、OpenSSL、libpcap、图像处理库、系统调用封装等。
- cgo 边界有明确的语言语义和运行时约束，例如 `C.CString`、`C.GoString`、Go pointer passing、`//export` callback。
- Go 自带的 `pprof` 更擅长展示 Go 函数层面的 CPU、heap、block 信息，但不直接解释跨 Go/C 边界上的转换、复制、pointer check 和 callback ping-pong。
- cgo 的使用者经常面临一个实际痛点：知道程序慢，但不知道慢在 C 函数本身、Go/C 边界、数据转换、pointer safety check，还是 callback 往返。

DrPy 是本工作的研究启发，而不是 CGOProf 的独立创新点。直接把 DrPy 的方法迁移到 cgo，只能说明场景变化，不能充分形成新的技术贡献。CGOProf 需要围绕 cgo 特有的调用、pointer passing、ownership/lifetime、callback 和 runtime safety contract，重新定义问题并发展新的分析与优化方法。

因此，本工作的核心 idea 被重新表述为：

> 将 cgo 边界低效定义为：动态跨语言交互在调用粒度、数据物化或 ownership/lifetime 管理上与 Go/C API contract 不匹配，从而产生了存在合法替代实现、可在保持语义的前提下消除的边界成本。CGOProf 对这种成本进行归因、解释，并在满足证明条件时自动优化。

这个问题视角把诊断粒度从函数耗时推进到“交互模式—成本成分—语义 contract—可优化性”四者之间的关系。一次 cgo 调用很小并不自动等于低效；一次复制、pointer check 或 callback 很频繁也不自动等于可消除。只有当工具能够说明成本在哪里、为什么重复、API contract 允许什么替代实现，以及 rewrite 需要满足哪些证明条件时，finding 才能从性能热点升级为可靠的优化机会。

论文中不应把“cgo 领域没有类似工具”单独当作贡献。更稳妥的定位是：在完成系统 related-work 调研后，使用“据我们所知，现有 Go profiler 与 cgo analysis 尚未联合提供交互语义级成本归因、ownership-aware 对象流重建以及基于 API contract 的优化合法性判断”作为研究空白陈述，而不是声称“第一个 cgo profiler”。

## 3. 应用场景

CGOProf 适用于使用 cgo 连接 Go 与 C library 的项目，尤其是 Go 层承担业务逻辑、C 层承担底层能力的系统。典型场景包括：

- 数据库驱动：例如 `go-sqlite3`，Go 层封装 SQLite C API。
- 系统库封装：例如网络抓包、压缩、加密、文件系统接口。
- 多媒体处理：例如图像、音频、视频编码解码库。
- 科学计算与高性能 native library 封装。
- 需要 C callback Go 的插件式或事件驱动库。

这些场景的共同特点是，单次 C 调用未必昂贵，但跨语言边界往返、数据转换、临时内存分配和安全检查可能在热点路径中被放大。尤其是在循环、批处理、数据库 statement 执行、逐行读取、逐字段转换等场景中，跨层开销可能累积成真实的性能瓶颈。

## 4. 工作意义：是否能解决真实痛点

这项工作的必要性来自三个方面。

第一，cgo 性能问题具有跨层性。传统 Go profiler 可以告诉开发者某些 Go 函数耗时高，但通常不会直接指出“这个 call site 上发生了 6 万次 `C.CString`，伴随 6 万次 C 分配和释放”，也不会自然区分 Go 到 C 的数据复制和 C 到 Go 的数据复制。开发者需要人工结合源码、pprof、runtime 语义和 C API 知识才能推断。

第二，cgo 边界热点呈现可归纳的交互模式。大量小粒度调用、重复表示物化、pointer guard 和 callback 往返不是单个项目特有的语法现象，可以被组织为可解释的候选模式。但这些模式是否普遍、是否覆盖主要问题，仍需通过多项目 empirical characterization 验证。

第三，部分边界成本存在合法替代实现，但优化受 contract 约束。例如：

- 小粒度调用可以通过 batch、cache、将循环移动到一侧来优化。
- `C.CString` 可以通过缓存稳定字符串、复用 C buffer、减少 prepare/exec 次数来优化。
- pointer check 可以通过减少 Go pointer passing 或在安全前提下研究 `#cgo noescape` 来优化。
- callback ping-pong 可以通过聚合回调或批量返回结果优化。
- C 到 Go 的重复复制可以通过缓存、延迟 materialization、批量解码或返回 handle 来优化。

目前工具已经在 synthetic examples 中证明基础 detector 能触发，并在 `go-sqlite3` 上提取出多个需要进一步确认的 L0/L1 candidate finding。基于当前 5 规则版本重新分析 `instrumented/go-sqlite3-v2/result/cgoprof.jsonl`，可得到 43 个候选：

- `small-call-detector`: 32
- `conversion-copy-detector`: 7
- `inbound-copy-detector`: 3
- `pointer-check-overhead-detector`: 1

这说明当前原型能够从真实 cgo 项目中提取可定位的边界热点，但不能据此声称 43 个 finding 全部是可消除低效。后续需要通过 contract、cost、graph 和人工/自动 rewrite 验证完成分级。

## 5. 问题定义与三类五模式 Taxonomy

### 5.1 从“热点”到“边界低效”

CGOProf 不把“调用次数多”“复制字节数大”或“pointer check 耗时可见”直接定义为低效。一个 cgo finding 分为三个证据等级：

| 等级 | 含义 | 工具可做出的结论 |
|---|---|---|
| `L0: observed hotspot` | 只观察到高频或高成本跨边界行为 | 报告事实，不声称成本可消除 |
| `L1: likely avoidable` | 观察到重复交互、重复物化或粒度不匹配，并找到可能的替代方式 | 输出优化候选和仍需确认的 contract |
| `L2: contract-proven rewritable` | 成本归因、API contract 和 rewrite proof obligations 均满足 | 允许生成候选 patch 并进入自动验证 |

本文将 **cgo boundary inefficiency** 定义为：

> 动态 Go/C 交互模式在调用粒度、数据物化或 ownership/lifetime 管理上与相关 API contract 不匹配，产生了可归因到语言边界、且存在语义保持替代实现的可消除成本。

因此，一个完整的诊断规则不只是阈值判断，而应写成：

```text
Rule = <Semantic Pattern,
        Interaction-Graph Motif,
        Attributed Cost,
        Contract Conditions,
        Avoidability Criterion,
        Candidate Rewrite,
        Proof Obligations>
```

### 5.2 主分类与正交标签

五种模式按照低效根因组织为三个互斥的上位类别：

```text
cgo Boundary Inefficiency
├── Control-Transfer Amplification
│   ├── P1 Forward Small-Call Amplification
│   └── P2 Reentrant Callback Amplification
├── Cross-Boundary Materialization Redundancy
│   ├── P3 Outbound Materialization Redundancy
│   └── P4 Inbound Materialization Redundancy
└── Ownership/Contract Enforcement Overhead
    └── P5 Avoidable Pointer-Guard Overhead
```

交互方向不是另一套顶层分类，而是每个 finding 的正交标签，因为同一个 `Go -> C` 方向可能同时包含 small call、outbound copy 和 pointer guard。建议为每个 finding 附加以下标签：

| 维度 | 典型取值 |
|---|---|
| 交互方向或形态 | `go_to_c`, `c_to_go`, `go_c_go_round_trip`, `alternating_callback` |
| 成本成分 | `transition`, `c_work`, `copy`, `allocation`, `free`, `pointer_guard`, `callback` |
| 语义 contract | `ownership`, `lifetime`, `escape`, `callback`, `mutability`, `representation` |
| 诊断置信度 | `observed`, `likely_avoidable`, `contract_proven` |

同一逻辑对象若在一条交互路径中先发生 outbound materialization、随后又发生 inbound materialization，Interaction Graph 可以组合产生 `round-trip materialization` 高级 finding。它暂不作为第六条基础规则，而是由 P3 和 P4 组成的跨规则 graph motif；是否提升为独立模式应由真实项目实证结果决定。

### 5.3 P1 Forward Small-Call Amplification

定义：Go 在热点路径中反复调用工作粒度很小的 C 函数，累计边界切换成本相对于实际 C work 过高，并且存在 batch、loop migration 或等价单侧实现。

```go
for i := 0; i < n; i++ {
    x += int(C.add_one(C.int(i)))
}
```

需要的关键证据包括调用次数、每次 boundary transition、C work、调用上下文中的循环结构，以及目标 C API 是否 callback、阻塞或依赖逐项副作用。只有“调用频繁且平均耗时小”时属于 L0；发现可批处理结构时进入 L1；证明迭代独立、顺序和副作用保持时才进入 L2。

优化方向：batch 调用、将循环整体移动到 Go 或 C 一侧、将简单 wrapper 内联为等价 Go 操作。

### 5.4 P2 Reentrant Callback Amplification

定义：Go 调用 C 后，C 在同一动态交互中频繁回调 Go，且每次 callback 工作很小，使正向和反向边界切换被重复放大。

```go
//export goCallback
func goCallback(v C.int) {
    results = append(results, int(v))
}

C.iterate_items(callback)
```

仅观察到高频 `//export` Go 函数不能证明 ping-pong。目标 detector 必须在图中重建重复的 `Go call site -> C function -> exported Go callback` 路径，并分解外层 C work、callback work 和双向 transition cost。

优化方向：在 C 侧聚合结果后一次返回、批量 callback、将 callback 工作统一移动到一侧。自动 rewrite 还需要证明回调顺序、可见副作用、错误传播和 reentrancy 语义保持。

### 5.5 P3 Outbound Materialization Redundancy

定义：热点路径中，同一或等价 Go 数据被重复物化为 C 表示，产生冗余 Go-to-C conversion、copy、C allocation 和 free。

```go
for _, query := range queries {
    cquery := C.CString(query)
    C.sqlite3_prepare_v2(db, cquery, ...)
    C.free(unsafe.Pointer(cquery))
}
```

该模式只覆盖 `C.CString`、`C.CBytes` 及其关联的 `malloc/memcpy/free`，不再包含 `C.GoString` 或 `C.GoBytes`。重复次数本身只形成 L0；要进入 L1/L2，还需要识别逻辑对象是否相同、数据在复用期间是否可变、C 是否保存 pointer、释放位置是否可安全移动。

优化方向：缓存稳定的 C representation、将转换提升到循环外、复用 C buffer、传递长度、使用 prepared-object cache 或批量接口。

### 5.6 P4 Inbound Materialization Redundancy

定义：热点路径中，同一或等价 native 数据被反复复制并物化为新的 Go `string` 或 `[]byte`，或者程序在不需要完整 Go object 时过早 materialize 数据。

```go
text := C.GoStringN((*C.char)(ptr), C.int(n))
```

该模式只覆盖 `C.GoString`、`C.GoStringN`、`C.GoBytes` 及其关联的 C-to-Go copy 和 Go allocation。识别冗余需要跟踪 native object identity、内容稳定性、owner、lifetime 和 Go 侧实际消费方式，而不能只依赖入站字节数阈值。

优化方向：缓存不可变结果、延迟 materialization、批量读取和解码、返回 native handle、只在真正需要 Go object 时转换。

### 5.7 P5 Avoidable Pointer-Guard Overhead

定义：Go pointer 或其派生 pointer 被高频传入 C，runtime safety guard 的成本显著；同时 contract analysis 能证明或高置信推断，目标 C 函数不会在调用后保存该 pointer、不会违反 lifetime、不会发生不允许的 callback，并存在合法的低成本传递方式。

```go
buf := make([]byte, n)
C.consume(unsafe.Pointer(&buf[0]), C.int(len(buf)))
```

pointer check 是维持 Go GC 与 pointer-passing 语义的必要机制，因此“检查次数高”只能产生 L0 finding。只有结合 `noescape`、`nocallback`、C body summary、Go escape/liveness 信息以及 ownership alternative 后，才能称为 avoidable overhead。

优化方向：批量传递、减少 Go pointer passing、使用 C-owned buffer，或在证明条件满足时生成 `#cgo noescape` / `#cgo nocallback` 候选 directive。任何自动修改都必须保守处理，并通过编译、测试、race 和动态验证。

## 6. 检测方法

当前基线检测方法可以概括为：

> 静态源码级插桩 + 动态运行期事件采集 + site-level 聚合 + 规则诊断。

目标方法在此基础上增加：

> Go/C contract analysis + boundary cost decomposition + ownership-aware graph + 分级 finding。

系统不是纯静态分析，也不是二进制级透明插桩。当前版本采用源码级 AST 改写，在程序实际运行时收集事件，因此能看到真实 workload 下发生了多少次调用、转换、内存操作、pointer check 和 callback；计划中的静态分析负责补足动态 profile 无法证明的 escape、callback、ownership 和 lifetime 条件。

### 6.1 静态扫描

扫描器读取 Go 源码，识别：

- `import "C"` 文件。
- `C.xxx(...)` call expression。
- `//export` Go callback。
- `#cgo noescape`、`#cgo nocallback` 等 directive。

扫描结果形成 callsite 列表，每个 callsite 包含：

- `site_id`
- 文件路径
- 行号
- 所在 Go 函数
- C symbol
- 原始表达式

### 6.2 源码级插桩

插桩器基于 Go AST 对项目副本进行 source-to-source rewriting。它不会修改原始项目，而是生成一个 instrumented copy。

对普通 C 调用，插桩后的形式大致为：

```go
end := prof.BeginCall(siteID, "sqlite3_step")
ret := C.sqlite3_step(stmt)
end()
```

对转换类调用，会额外插入 conversion 和 memory event：

```go
prof.Conversion(siteID, "C.CString", len(s)+1)
prof.Memory(siteID, "malloc", len(s)+1)
cs := C.CString(s)
```

对 `C.GoStringN`、`C.GoBytes`，runtime 会将方向标记为 `c_to_go`，用于 `inbound-copy-detector`。

对 Go pointer passing，插桩器识别直接或简单间接的 `unsafe.Pointer(&buf[0])` 等 Go pointer 来源，并插入 pointer check 估计事件。

对 `//export` callback，插桩器在导出的 Go 函数中记录 callback 事件。

### 6.3 运行期事件

运行插桩项目时，runtime recorder 输出 JSONL 事件。主要事件类型包括：

- `cgo_call`
- `conversion`
- `memory`
- `pointer_check`
- `callback`
- `scheduler`

其中 `scheduler` 事件目前作为底层事件保留，但不再对应单独检测规则。

### 6.4 Contract-Aware cgo Semantic Analysis

目标版本需要为每个 C function、argument、return value 和 callback 建立 contract summary。核心维度包括：

| Contract 维度 | 需要回答的问题 | 可能的信息源 |
|---|---|---|
| ownership | 参数或返回对象由 Go、C 还是 caller/callee 管理？谁负责释放？ | wrapper pattern、C header/body、free site、人工 annotation |
| lifetime | C 访问对象是否仅限当前调用？返回 pointer 在何时失效？ | C body summary、调用后 use、API documentation annotation |
| escape | C 是否保存传入的 Go pointer，或把它写入全局/heap/返回值？ | `#cgo noescape`、C AST/IR、points-to summary |
| callback | C 函数是否直接或间接回调 Go？ | `#cgo nocallback`、function pointer flow、动态 callback edge |
| mutability | C 是否修改输入 buffer？数据在缓存期间是否稳定？ | type qualifier、write summary、动态版本或 hash 证据 |
| representation | API 是否要求 NUL termination、长度参数、alignment 或特定 layout？ | 函数签名、wrapper code、annotation |

contract 结果需要携带 provenance 和置信度，例如 `declared`、`statically_proven`、`dynamically_observed`、`user_asserted`、`unknown`。动态运行中未观察到 escape 或 callback 不能单独作为不存在该行为的证明；缺少可靠信息时必须降级为 L0/L1 并要求人工审核。

当前实现已经完成七属性 Contract IR，以及内容寻址的 API Identity、package-local `C.name` binding、精确 Build Manifest、unresolved/ambiguity 隔离和 Contract–Manifest fail-closed 链接；项目发现器可从 `go env`/`go list` 建立真实构建快照，并精确识别 cgo intrinsic。任意外部 C API 的 provider/ABI canonical signature 提取、C body effect summary 和完整 contract inference 仍需在后续静态分析阶段实现。

### 6.5 Boundary Cost Decomposition

目标分析不只记录 cgo 调用总时间，而是为一次交互估计：

```text
T_interaction = T_transition
              + T_c_work
              + T_materialization
              + T_allocation_free
              + T_pointer_guard
              + T_callback
              + epsilon
```

其中：

- `T_transition`：Go runtime 与 C ABI 之间的正向或反向切换成本。
- `T_c_work`：C 函数本体执行的有效工作。
- `T_materialization`：Go/C representation conversion 和数据复制。
- `T_allocation_free`：Go/C 临时对象分配、释放及相关 GC/allocator 压力。
- `T_pointer_guard`：pointer passing safety enforcement。
- `T_callback`：callback 内 Go work 与反向 transition。
- `epsilon`：插桩、测量误差和当前模型无法解释的残差。

可结合嵌套计时、转换/内存事件、callback span、runtime/benchmark calibration 和差分实验完成归因。论文必须单独评估插桩开销、各成本成分的误差，以及分解结果是否能稳定支持 finding ranking。

当前原型已经收集 call、conversion、memory、pointer-check 和 callback 聚合指标，但尚不能完整分离 `T_transition` 与 `T_c_work`，因此现有 `boundary_ratio` 应被明确标记为估计值。

### 6.6 Ownership-Aware CGO Interaction Graph

基础 CGO Interaction Graph 已能关联 callsite 与运行期事件；目标版本进一步把“事件图”升级为“对象、contract 和动态交互图”。建议的节点包括：

- Go function 与 cgo call site。
- C function 与 exported Go callback。
- logical object 及其 Go/C representation。
- conversion、copy、allocation、free、pointer guard。
- contract summary 与 proof evidence。

建议的边包括：

- `calls` / `callbacks_to`：正向与反向控制转移。
- `materializes_as` / `copies_to`：对象跨 representation 的物化与复制。
- `passes_pointer_to` / `may_escape_to`：pointer 传递与逃逸关系。
- `owns` / `borrows` / `frees`：ownership 和 lifetime 关系。
- `guarded_by` / `justified_by`：运行时检查和 contract 证据。

图应保留动态 execution/context id 或等价嵌套关系，才能区分独立 callback 与真实 `Go -> C -> Go` ping-pong；还应为对象建立 identity 或 conservative alias set，才能判断两次转换是同一逻辑数据的重复物化，而不只是发生在同一个源码位置。

五个基础模式在图中对应可查询 motif：

- 重复 `Go site -> small C function` 边对应 P1。
- 高频 `Go site -> C function -> Go callback` 嵌套路径对应 P2。
- 同一 Go object 多次 `materializes_as` C representation 对应 P3。
- 同一 native object 多次 `materializes_as` Go representation 对应 P4。
- `Go object -> passes_pointer_to -> C function` 且伴随 guard/contract 证据对应 P5。

### 6.7 分级规则诊断

当前规则首先在每个 site 的聚合指标上运行。核心指标包括：

- `call_count`
- `total_cgo_ns`
- `avg_cgo_ns`
- `conversion_count`
- `conversion_bytes`
- `inbound_conversion_count`
- `inbound_conversion_bytes`
- `malloc_count`
- `free_count`
- `pointer_check_count`
- `pointer_check_ns`
- `callback_count`
- `callback_ns`

目标版本需要把 site-level 聚合与 graph motif、contract 和 cost attribution 联合起来。每条规则输出：

- rule name
- taxonomy family 与 semantic pattern
- severity
- evidence level：L0/L1/L2
- site id
- direction/interaction-shape tags
- summary
- attributed cost 与测量置信区间
- graph evidence
- contract evidence、provenance 和 unknown conditions
- recommendation
- candidate rewrite 与 proof obligations

## 7. 五条基础规则的目标规范

| 模式 / 当前 detector | L0 动态信号 | 升级为 L1/L2 所需语义证据 | 典型 rewrite | 核心 proof obligations |
|---|---|---|---|---|
| P1 Forward Small-Call / `small-call-detector` | `call_count`, `avg_cgo_ns`, `T_transition/T_interaction` | loop/context、C work 很小、存在 batch 或 loop migration 方案 | batch、move loop、等价 Go implementation | 迭代独立性、顺序、副作用、错误和阻塞语义保持 |
| P2 Reentrant Callback / `callback-pingpong-detector` | `callback_count`, `callback_ns` | 重建 `Go -> C -> Go` 嵌套路径，callback work 相对很小 | aggregate callback、batch result | callback 顺序、reentrancy、异常/错误传播、可见副作用保持 |
| P3 Outbound Materialization / `conversion-copy-detector` | outbound conversion/copy/alloc/free 次数和字节数 | 相同 Go object/value、稳定性、C 不越界保存、free 可移动 | hoist/cache C representation、reuse buffer | mutability、ownership、lifetime、NUL/length/layout 要求满足 |
| P4 Inbound Materialization / `inbound-copy-detector` | inbound conversion/copy/Go allocation 次数和字节数 | 相同 native object、内容稳定、Go 侧消费不要求立即拥有完整副本 | cache、lazy materialization、batch decode、native handle | native lifetime、并发修改、Go ownership 与 API 可见语义保持 |
| P5 Avoidable Pointer Guard / `pointer-check-overhead-detector` | `pointer_check_count`, `pointer_check_ns`, guard ratio | noescape/nocallback/liveness/ownership 证据，或合法 C-owned alternative | batch、C-owned buffer、候选 directive | C 不保存 pointer、无非法 callback、lifetime 与 GC safety 保持 |

规则排序不应只依赖固定阈值，而应综合 `avoidable_cost × execution_frequency × confidence × rewrite_feasibility`。阈值只负责筛选 L0 候选，不能替代对“是否冗余、是否可优化”的语义判断。

## 8. 工具运行流程

工具的目标形态不是只输出 finding，而是形成一条 contract-aware、带证明条件的端到端闭环：

> 静态扫描与插桩 -> 运行 workload -> contract inference 与成本分解 -> 构建 ownership-aware graph -> 分级诊断 -> 生成 proof obligations -> 候选 rewrite -> 语义与性能验证 -> 输出可审计报告。

当前原型已经完成自动插桩、基础检测、定位、报告和示例级 benchmark；contract inference、对象级图、proof-obligation discharge 和通用 patch 生成尚未实现。论文实验必须明确区分“人工构造 optimized variant 证明优化空间”和“工具自动生成 rewrite”两种能力。

### 8.1 当前原型流程：插桩与检测

#### 8.1.1 扫描项目

```bash
cd /Users/ban/Documents/Projects/drpy/cgoprof
python3 -m cgoprof scan path/to/go-project
```

输出项目中的 cgo call sites。

#### 8.1.2 生成插桩项目

```bash
python3 -m cgoprof instrument path/to/go-project --out instrumented/project-name --force
```

该命令会：

- 复制原项目到输出目录。
- 使用 Go AST 改写 cgo call sites。
- 在 `go.mod` 中加入本地 runtime recorder 的 `replace` directive。

#### 8.1.3 运行 workload

```bash
cd instrumented/project-name
CGOPROF_OUT=result/cgoprof.jsonl go test ./...
```

也可以运行项目自己的 benchmark、集成测试或真实 workload。检测结果依赖 workload 覆盖范围。

#### 8.1.4 分析 profile

```bash
cd /Users/ban/Documents/Projects/drpy/cgoprof
python3 -m cgoprof analyze instrumented/project-name/result/cgoprof.jsonl \
  --root instrumented/project-name \
  --graph-out instrumented/project-name/result/interaction_graph.json
```

也可以输出 JSON：

```bash
python3 -m cgoprof analyze instrumented/project-name/result/cgoprof.jsonl \
  --root instrumented/project-name \
  --json > instrumented/project-name/result/findings.json
```

当前分析结果包含基础 finding、源码位置和聚合证据；目标分析结果应包含：

- taxonomy family、semantic pattern 和 L0/L1/L2 等级。
- 源码位置。
- 触发该 finding 的动态成本证据及测量置信度。
- Ownership-Aware Graph 中关联的 C 函数、对象、转换、内存操作、pointer guard、callback 和 contract。
- 可行优化建议、未知条件和 proof obligations。

### 8.2 完全体目标流程：检测后自动进入优化闭环

完全体 CGOProf 应在 `analyze` 之后继续执行优化决策和验证。可以设计为一个更高层命令，例如：

```bash
python3 -m cgoprof optimize path/to/go-project \
  --workload "go test ./..." \
  --bench "go test -bench=. -run=^$" \
  --out optimized/project-name
```

该命令在内部执行以下阶段。

#### 8.2.1 Finding 分类：由证据等级决定自动化边界

工具根据证据等级而不是规则名称决定 finding 的自动化边界：

| 类别 | 含义 | 允许的处理方式 |
|---|---|---|
| L2 自动候选优化 | 所有静态 proof obligations 已满足，且动态条件可被验证 | 在临时副本生成 patch，运行全套验证；验证失败则丢弃并降级 |
| L1 人工审核后优化 | 找到高收益 rewrite，但 ownership、lifetime、callback 或 API 语义仍有未证明条件 | 输出候选 patch、未知条件和人工确认项，不默认应用 |
| L0 仅报告热点 | 只有动态成本证据，尚不能证明冗余或存在合法替代实现 | 输出定位、成本分解和继续分析建议，不称为已确认低效 |

这里的核心原则是：自动化权限由证明状态决定。同一条 P3 finding 可能是 L0、L1 或 L2；即使是看似简单的 string cache，只要 mutability 或 lifetime 未知，也不能自动应用。

#### 8.2.2 Proof obligations

每个 rewrite template 应声明机器可检查或需要人工确认的条件：

- control-flow：调用次数变化不破坏顺序、错误传播、阻塞和外部副作用。
- data equivalence：转换前后 representation、长度、NUL termination、alignment 和内容一致。
- ownership/lifetime：对象在所有使用点仍有效，且恰好由合法 owner 释放一次。
- pointer safety：C 不非法保存 Go pointer，修改不会破坏 Go GC 假设。
- callback/concurrency：callback 顺序、reentrancy、线程亲和性和同步语义保持。
- resource behavior：不存在新增 leak、double free、use-after-free 或 handle 生命周期变化。

证明条件的来源必须记录在优化报告中。静态证明、显式 directive 或经过验证的库 summary 可以支持 L2；单次 workload 的动态“未观察到”只能作为辅助证据。

#### 8.2.3 自动优化策略

对于不需要人工审核的 finding，工具可以根据模式应用优化模板。

P1 Forward Small-Call 的自动优化设想：

- 如果工具发现 Go 循环中重复调用同一个小 C 函数，并且存在可用 batch API，则将 per-item cgo call 改为 batch call。
- 如果小 C 函数逻辑简单且可等价表示为 Go 代码，则建议或自动替换为 Go 实现。
- 如果循环可以安全移动到 C 侧，则生成 C helper，将多次边界跨越合并为一次。

P3 Outbound Materialization 的自动优化设想：

- 对循环中重复创建的稳定 `C.CString`，将其提升到循环外，并在合适位置统一 `C.free`。
- 对反复分配的 C buffer，生成复用 buffer 或 buffer pool。
- 对 prepare/execute 类模式，优先建议 statement cache 或 prepared statement reuse。

P4 Inbound Materialization 的自动优化设想：

- 对重复读取不可变 C 字符串的路径，生成缓存。
- 对逐项 `C.GoStringN` / `C.GoBytes`，如果 C API 支持批量返回，则改为批量 decode。
- 对仅用于比较或转发的 C string，尽量避免立即 materialize 成 Go string。

P2 Callback 和 P5 Pointer Guard 也可以拥有 rewrite template，但它们通常包含更强的 reentrancy、escape 和 lifetime 条件，应优先作为 L1 候选，仅在证据完整时升级到 L2。

当前原型尚未实现通用自动 patch 生成，但已有 benchmark 展示了这些模式具有真实优化空间。后续论文中应把“带证明条件的候选 rewrite + 自动验证”实现为核心方法，而不只是附加功能。

#### 8.2.4 人工审核项的处理

对于 pointer 相关 finding，工具不应直接自动修改，因为错误优化可能破坏 Go runtime 和 GC 的安全假设。

例如 P5 Avoidable Pointer-Guard finding 应输出：

- 哪个 call site 频繁传入 Go pointer。
- pointer check 次数和估计成本。
- 该 C 函数是否已有 `#cgo noescape` 或 `#cgo nocallback` directive。
- 人工需要确认的问题：
  - C 函数是否保存 Go pointer？
  - C 函数是否在调用返回后继续访问该 pointer？
  - C 函数是否可能 callback Go？
  - Go object 生命周期是否覆盖 C 侧访问？

只有当这些条件被确认满足时，工具才可以建议进一步使用 `#cgo noescape`、减少 pointer passing，或改用 C-owned buffer。

#### 8.2.5 副作用验证

自动优化后，工具必须验证 patch 不改变程序语义。验证应至少包括：

- 编译验证：`go test` 或 `go test ./...` 必须通过。
- 单元测试验证：项目原有测试必须通过。
- 可选 race 检查：对涉及 pointer、callback 或共享 buffer 的优化运行 `go test -race`。
- profile 对比：优化后相关 event count 应下降，例如 cgo call count、conversion count、pointer check count 或 callback count。
- 输出一致性检查：对 benchmark 或示例 workload 比较关键输出。

如果任何验证失败，工具应丢弃临时副本中的 patch，并将该 finding 标记为“候选优化未通过动态验证”。需要强调：测试通过只能反驳已覆盖路径上的错误，不能替代静态 proof obligations。

#### 8.2.6 加速比计算

优化验证通过后，工具应自动运行优化前后 benchmark，并输出：

```text
speedup = baseline_median_time / optimized_median_time
```

报告中应同时给出：

- baseline 时间。
- optimized 时间。
- speedup。
- 运行次数和 warmup 次数。
- 被优化的 finding。
- 优化前后关键事件数量变化。
- 是否通过测试。

目标报告还应记录证据等级和 proof 状态，例如：

| Finding | Pattern | Level | Optimization | Proof status | Event Reduction | Speedup | Validation |
|---|---|---|---|---|---:|---:|---|
| `sqlite3.go:2206 C.CString` | P3 outbound materialization | L2 | cache C representation | lifetime/mutability proven | conversion -80% | 2.28x | tests passed |
| small call loop | P1 forward small-call | L2 | batch C calls | order/side effects proven | cgo calls -95% | 5.25x | tests passed |

### 8.3 当前已实现的优化验证能力

当前工具原型已经具备示例级优化验证，而不是完整真实项目自动 patch：

- `benchmarks/` 中提供了 baseline/optimized 成对程序。
- `benchmarks/run_benchmarks.py` 可以运行多轮 benchmark。
- `benchmarks/results/speedups.md` 输出加速比。

已有结果显示：

| Case | Baseline median | Optimized median | Speedup |
|---|---:|---:|---:|
| `small_calls` | 20.714 ms | 3.948 ms | 5.25x |
| `conversion_copy` | 17.162 ms | 7.532 ms | 2.28x |
| `pointer_check` | 9.876 ms | 6.633 ms | 1.49x |
| `callback_pingpong` | 9.739 ms | 5.555 ms | 1.75x |

这些受控实验说明四种候选模式可以产生可测量的优化收益，但尚不能证明真实项目中的任意同类 finding 都可优化。后续工作的重点是把“手写 optimized variant + benchmark”推进为“contract/graph 证明条件 + 工具生成候选 patch + 自动验证 + 真实项目 speedup”的完整闭环。

### 8.4 完整验证

```bash
cd /Users/ban/Documents/Projects/drpy/cgoprof
PYTHONPATH=. GOCACHE=/private/tmp/go-build-cache ./run_all.sh
```

当前完整验证包括：

- Python 单元测试。
- synthetic all-rule profile。
- 四个 cgo 示例。
- analyzer rule 检查。
- 优化前后 benchmark。

最近一次验证结果为：

```text
Ran 8 tests
OK
CGOProf full verification passed.
```

## 9. 已完成产出

### 9.1 工具代码

- Python CLI：`cgoprof/cli.py`
- 扫描器：`cgoprof/scanner.py`
- 事件模型：`cgoprof/models.py`
- Interaction Graph：`cgoprof/graph.py`
- 规则系统：`cgoprof/rules.py`
- 文本报告：`cgoprof/report.py`
- Go AST 插桩器：`instrumenter/cgoprof-instrument/main.go`
- Go runtime recorder：`runtime_go/cgoprof/cgoprof.go`

### 9.2 示例与测试

- `examples/small_calls`
- `examples/conversion_copy`
- `examples/pointer_check`
- `examples/callback_pingpong`
- `examples/profiles/synthetic_all_rules.json`
- `tests/test_rules.py`
- `tests/test_instrumenter.py`

### 9.3 Benchmark

已有四类人工构造的 baseline/optimized benchmark，用于说明这些模式在受控场景中存在优化空间，而不是证明任意同类 finding 都可优化：

| Case | Baseline median | Optimized median | Speedup |
|---|---:|---:|---:|
| `small_calls` | 20.714 ms | 3.948 ms | 5.25x |
| `conversion_copy` | 17.162 ms | 7.532 ms | 2.28x |
| `pointer_check` | 9.876 ms | 6.633 ms | 1.49x |
| `callback_pingpong` | 9.739 ms | 5.555 ms | 1.75x |

### 9.4 go-sqlite3 实验

已对 `go-sqlite3` 进行插桩并收集运行期 profile：

- 插桩项目：`instrumented/go-sqlite3-v2`
- 原始 profile：`instrumented/go-sqlite3-v2/result/cgoprof.jsonl`
- workload：`go test ./...`
- runtime events：2,204,007
- static cgo call sites：239
- runtime metric sites：254

使用当前 5 规则版本重新分析该 profile，得到 43 个 candidate finding：

- `small-call-detector`: 32
- `conversion-copy-detector`: 7
- `inbound-copy-detector`: 3
- `pointer-check-overhead-detector`: 1

这些结果来自现有阈值 detector，尚未经过目标 contract/cost/graph 分级。论文中应将其报告为基线候选，并在新分析完成后给出 L0/L1/L2 分布和人工确认结果。

## 10. 技术路线与使用技术

### 10.1 Go AST source-to-source rewriting

工具使用 Go 标准库 AST 能力改写源码。选择源码级插桩的原因是：

- 实现成本低于二进制插桩或 runtime patch。
- 能直接保留源码位置，便于报告定位。
- 能识别 cgo 语法结构和 Go 表达式上下文。
- 便于在研究原型阶段快速迭代规则和事件。

### 10.2 Lightweight Runtime Recorder

插桩后的代码调用 `cgoprof/runtime_go/cgoprof` 中的 recorder。该 recorder 负责：

- 记录时间戳。
- 记录 goroutine id。
- 记录事件类型和 site id。
- 输出 JSONL profile。

该设计避免声称透明 runtime interception，而是选择可测试、可解释的显式事件记录。

### 10.3 Python Analyzer

Python 侧负责：

- 解析 JSON/JSONL profile。
- 聚合 site metrics。
- 构建 CGO Interaction Graph。
- 执行规则。
- 输出 text/json/graph 报告。

将 analyzer 写在 Python 中的好处是规则迭代快，适合研究阶段进行实验、统计和报告生成。

### 10.4 Rule-Based Diagnosis

当前采用 site-level rule-based diagnosis，原因是五种模式具有可解释的语义结构，便于在研究原型阶段建立基线。目标版本不是简单增加更多阈值，而是把每条规则实现为 graph motif、cost attribution、contract condition 和 proof obligation 的组合。

### 10.5 计划增加的静态与跨语言分析

为支持 contract-aware 诊断，需要引入 Go SSA/escape/liveness、C AST/IR summary、轻量 points-to/alias、callback target resolution 以及可审计的人工/library annotation。分析可以是 conservative 的：无法证明时降低 finding 等级，而不是冒险生成 rewrite。

### 10.6 计划增加的对象与执行上下文跟踪

runtime event schema 需要加入 execution/context id、parent span、object/representation id、allocation/free id 和 contract reference。这样才能从 site-level 统计升级到对象流与嵌套交互分析，并控制 instrumentation overhead。

### 10.7 Rewrite Engine 与 Validator

rewrite engine 维护 pattern-specific template、applicability predicate 和 proof obligations；validator 在临时项目副本中执行 build、tests、race、sanitizer（适用时）、output comparison、profile comparison 和 statistically sound benchmark。任何失败都不修改原始项目。

## 11. 面向 CGO 2027 的四项核心创新

### 11.1 创新一：cgo 边界低效的问题定义与系统化分类

CGOProf 将 cgo 边界低效形式化为“动态交互模式与 Go/C API contract 不匹配所产生的可消除成本”，把诊断对象从函数级热点推进到交互语义、成本根因和可优化性的联合判断。

在该定义下，本文提出三个上位类别和五个互斥基础模式：

- Control-Transfer Amplification：P1 forward small-call、P2 reentrant callback。
- Cross-Boundary Materialization Redundancy：P3 outbound、P4 inbound materialization。
- Ownership/Contract Enforcement Overhead：P5 avoidable pointer guard。

方向、成本成分和 contract 类型作为正交标签，避免把 Go-to-C/C-to-Go 与低效根因混为两套竞争 taxonomy。该贡献需要通过多项目实证研究说明五种模式的普遍性、互斥性、覆盖范围和实际优化价值；只有五个 heuristic detector 本身不足以支撑问题定义创新。

当前状态：五种候选模式和基础 detector 已存在，但 taxonomy 的系统实证、对象级 redundancy 判断、graph motif 和 L0/L1/L2 证据分级仍需实现。

### 11.2 创新二：Contract-Aware cgo Semantic and Boundary-Cost Analysis

本文计划联合静态语义分析与动态 profiling，为 cgo API 推断 ownership、lifetime、escape、callback、mutability 和 representation contract，并将一次交互成本分解为 transition、C work、materialization、allocation/free、pointer guard 和 callback。

技术新颖性不在于多记录几个事件，而在于回答两个传统 profiler 难以联合回答的问题：

1. 可见总时间究竟由 C 本体工作还是边界语义成本造成？
2. 某项成本是必要的 contract enforcement，还是在当前 API contract 下可安全避免？

该分析把“高频行为”与“可消除低效”分开，并为 finding 及后续 rewrite 提供可审计证据。当前状态：动态事件、七属性 Contract IR、API Identity/Build Manifest、部分 directive/pointer pattern 已实现；外部 C declaration/body summary、完整 contract inference、成本校准与误差评估尚未实现。

### 11.3 创新三：Ownership-Aware CGO Interaction Graph

本文计划提出 ownership-aware graph，统一表示跨语言控制流、logical object、Go/C representation、copy/materialization、allocation/free、pointer passing、callback、owner、lifetime 和 contract evidence。

与仅连接 call site 和事件类型的基础图相比，目标图能够表达和查询：

- 同一对象是否被重复物化为另一侧 representation。
- `Go -> C -> Go` callback 是否属于同一动态交互。
- pointer、owner、borrow 和 free 的跨语言关系。
- 一条 finding 由哪些 contract 和 cost evidence 支撑。
- outbound 与 inbound 行为是否组成 round-trip materialization。

该图不仅用于可视化，而是五条规则的分析中间表示，并为路径级诊断、对象级归因和 rewrite legality 提供共同基础。当前状态：site/event-level CGO Interaction Graph 已实现；object identity、alias、ownership/lifetime edge、context nesting 与 contract node 尚未实现。

### 11.4 创新四：Proof-Obligation-Guided Profile-Driven Rewrite

本文计划把 profiling finding 转换为带显式合法性条件的优化：每个 rewrite template 同时声明适用模式、预期消除的成本、所需 contract、proof obligations 和验证过程。只有全部静态条件满足的 L2 finding 才进入自动候选 rewrite；其余 finding 输出未知条件或人工审核项。

该方法试图弥合“profiler 能指出热点，但无法保证优化合法”和“编译器能证明局部变换，但不知道哪个变换值得做”的鸿沟：profile 决定收益优先级，contract/graph 决定合法性，自动验证检查未被静态模型覆盖的工程风险。

当前状态：已有人工编写的 baseline/optimized 示例和 benchmark；通用 template、proof-obligation checker、patch generator、失败回退和真实项目自动优化尚未实现。

### 11.5 四项创新之间的逻辑关系

```text
Problem Formulation and Taxonomy
    定义什么是 cgo 边界低效
        -> Contract-Aware Cost Analysis
           判断成本来自哪里、是否可能避免
              -> Ownership-Aware Interaction Graph
                 重建对象、路径与证明证据
                    -> Proof-Guided Rewrite
                       只优化可证明合法且 profile 显示值得优化的 finding
```

四项贡献应各自产生可独立评价的研究产物：

| 创新点 | 主要产物 | 关键评价问题 |
|---|---|---|
| 问题定义与 taxonomy | 语义定义、三类五模式、真实项目 corpus | 分类是否覆盖真实问题、是否比函数热点更有解释力？ |
| Contract-aware cost analysis | contract summary、成本模型、归因算法 | contract 和成本归因是否准确？能否减少误报？ |
| Ownership-aware graph | object/context graph、motif query | 是否能正确重建重复物化、callback 往返和 ownership flow？ |
| Proof-guided rewrite | rewrite templates、proof checker、validator | 自动 patch 是否安全、成功率和实际 speedup 如何？ |

### 11.6 不应单独声称为核心创新的内容

- “将 DrPy 迁移到 cgo”是研究启发，不是独立贡献。
- “使用 Go AST 自动插桩”是支撑实现，除非提出新的低开销或语义保持插桩方法。
- “实现五条规则”只有在规则对应系统 taxonomy、具有 contract/cost 语义并经实证验证时才构成问题定义贡献。
- “cgo 领域没有类似工具”是需要 related-work 支撑的定位陈述，不是贡献本身；应避免未经验证的绝对 first claim。

## 12. 局限性

### 12.1 当前不是透明 profiler

工具依赖源码级插桩，需要生成 instrumented copy 并重新运行 workload。相比二进制插桩或 runtime-level profiler，它侵入性更高。

### 12.2 检测依赖 workload 覆盖

动态 profile 只能看到运行过的路径。如果测试或 benchmark 没覆盖某个 cgo 热点，工具不会报告该路径的问题。

### 12.3 当前不是完整 SSA/points-to/side-effect analysis

当前指针来源分析是 AST 级和轻量 helper summary，不是完整 SSA、points-to、escape 或 C side-effect analysis。因此复杂 alias、indirect callback 和跨函数数据流下可能漏报或归因不准。

### 12.4 Contract inference 的可靠性边界

工具目前不能可靠证明 C 函数是否保存 Go pointer、是否间接 callback Go、是否修改或异步使用 buffer。未来即使加入静态分析，外部闭源库、inline assembly、function pointer 和不完整 build configuration 仍可能导致 `unknown`。系统必须允许保守降级和人工/library annotation。

### 12.5 成本分解存在测量误差

嵌套计时和源码插桩可能扰动极短 cgo call；`T_transition` 与 `T_c_work` 也难以在所有平台上完全分离。论文需要报告 instrumentation overhead、calibration method、残差和跨硬件稳定性。

### 12.6 对象身份与 ownership 重建可能不完备

同一逻辑值可能由不同 address 表示，address 也可能被 allocator 重用。缺少可靠 object identity 时，重复物化只能保守报告为候选。ownership/lifetime edge 的错误会直接影响 rewrite 安全性，因此图必须携带置信度与证据来源。

### 12.7 测试通过不等于语义证明

动态测试、race detector 和 benchmark 只能覆盖实际运行路径。它们是 rewrite validation 的必要组成，但不能替代静态 proof obligations；工具不应因测试通过就把 L1 finding 自动升级为 L2。

### 12.8 阈值与 ranking 仍需系统化校准

当前规则阈值基于经验和示例验证。后续需要在多项目 corpus 上校准 precision、recall、cost attribution 和 finding ranking，并进行 ablation，判断 contract、graph 和 cost decomposition 分别减少了多少误报。

### 12.9 历史实验结果需要和规则版本对应

工具迭代过程中曾短暂加入 `blocking-cgo-detector` 和 `c-allocation-churn-detector`，后来删除。引用历史结果时需要确认报告是由哪个规则版本生成。当前工具代码只保留 5 条规则。

## 13. 评价计划与 Research Questions

评价必须分别验证四项创新，而不能只展示若干 benchmark speedup。

### 13.1 RQ1：问题是否普遍，三类五模式是否有效？

建立覆盖数据库、网络、压缩/加密、多媒体、科学计算和系统 wrapper 的真实 cgo corpus。对项目进行静态扫描、workload profiling 和人工审计，统计：

- 每种模式涉及的项目数、call site 数和动态成本占比。
- 五种模式之外的真实边界低效数量，用于评价 taxonomy coverage。
- 不同 reviewer 对 finding 分类的一致性。
- 同一 site 上多种模式共存的情况及其因果关系。
- 可优化 finding 与仅有高频行为之间的比例。

该 RQ 是“问题视角创新”的主要证据，不能只依赖 synthetic examples 或单一 `go-sqlite3` case study。

### 13.2 RQ2：Contract inference 是否准确且足够保守？

为 selected C APIs 人工建立 ownership、lifetime、escape、callback、mutability gold summaries，比较工具结果：

- precision、recall 和 `unknown` rate。
- 错误 contract 对 finding 等级和 rewrite decision 的影响。
- 显式 directive、C body analysis、Go analysis、library annotation 各自的贡献。
- 对 indirect call、external library 和 incomplete source 的降级行为。

对于安全相关 contract，应优先控制不安全的 false proof，而不是只追求高 recall。

### 13.3 RQ3：边界成本分解是否准确？

使用可控 microbenchmark、差分实现和硬件计数/独立 profiler（适用时）验证各成本成分：

- attributed cost 与 ground-truth/differential cost 的绝对和相对误差。
- `T_transition`、`T_c_work`、copy/alloc、guard、callback 的可分辨性。
- ranking 在不同 workload、输入规模、Go version、OS/architecture 上的稳定性。
- instrumentation overhead、残差 `epsilon` 和短调用测量扰动。

### 13.4 RQ4：Ownership-Aware Graph 是否正确重建跨语言交互？

对带 ground truth 的 synthetic/real traces 评价：

- callback parent-child path 的 precision/recall。
- object/representation identity 和重复物化判断的准确性。
- owner、borrow、free、escape 和 lifetime edge 的准确性。
- round-trip materialization 等组合 motif 的识别能力。
- 图规模、构建时间和内存开销。

### 13.5 RQ5：完整诊断比现有基线和简单阈值多提供什么？

在相同 workload 下比较通用 Go profiling/cgo 统计、当前 site-level detector 和完整 CGOProf，评价：

- verified true positives、false positives、false negatives。
- top-k finding 的人工确认率和实际 avoidable cost。
- 定位到源码、对象、contract violation 和优化建议所需的人工时间。
- ablation：依次去掉 cost decomposition、contract、ownership graph 和 proof conditions，观察精度与排名变化。

### 13.6 RQ6：Proof-guided rewrite 是否安全且有效？

对每类 rewrite 统计：

- L0/L1/L2 finding 数量及升级/降级原因。
- 自动生成 patch 数、proof discharge rate、编译/测试/race 通过率。
- 人工审核后确认或拒绝的比例，以及拒绝原因。
- 语义回归、memory-safety failure、leak 和 invalid directive 数量。
- baseline/optimized wall time、`ns/op`、事件减少量和 speedup 置信区间。

除 synthetic benchmark 外，至少需要若干真实项目 patch，并尽可能通过项目 maintainer review 或等价的外部有效性验证。

### 13.7 RQ7：系统开销与可用性如何？

报告静态分析时间、插桩时间、运行时 slowdown、profile 大小、图构建内存和端到端分析时间。分别测量各事件类型和 context/object tracking 的增量开销，并讨论 sampling 或 selective instrumentation 的取舍。

## 14. 面向论文的实施路线

### 14.1 阶段一：冻结问题定义与基线

- 将代码中的 `conversion-copy-detector` 收窄为 Go-to-C outbound materialization，确保与 inbound rule 不重叠。
- 为五条规则加入 taxonomy family、direction、cost component 和 L0/L1/L2 字段。
- 建立 positive、hard-negative 和 non-optimizable examples；特别加入“高频但必要”的反例。
- 用当前五规则版本重新生成 `go-sqlite3-v2` 的 `findings.json`、`report.txt`、`summary.md` 和 CSV，标记历史七规则结果。

### 14.2 阶段二：真实项目问题刻画

- 扩展到多个领域的 cgo 项目，建立可复现实验 corpus 和 workload。
- 人工审计边界热点，验证三类五模式，并记录 taxonomy 之外的新模式。
- 先形成一项独立 empirical result，再决定是否增加或合并规则，避免凭直觉扩展 detector 数量。

### 14.3 阶段三：Contract 与成本分析

- 实现 Go SSA/escape/liveness 与 C function summary 的最小闭环。
- 推断 noescape/nocallback、read/write、ownership/lifetime，并记录 unknown/provenance。
- 设计并校准 boundary cost decomposition；更精确地区分 transition、C work、pointer guard 和 callback。
- 用 contract 和 cost evidence 把现有 threshold finding 从 L0 升级到 L1/L2。

### 14.4 阶段四：Ownership-Aware Graph

- 扩展 event schema，加入 context nesting、object/representation、allocation/free identity。
- 实现 ownership/lifetime/escape edge 和 conservative alias set。
- 将 P2–P5 重写为 graph motif query，并实现 round-trip materialization 组合 finding。
- 优先验证图的分析能力；可视化界面属于次要工程任务。

### 14.5 阶段五：Proof-Guided Rewrite

- 首先实现 proof obligations 较清晰的 P3 hoist/cache 和部分 P1 batching template。
- 再实现 P4 lazy/batch materialization；P2/P5 默认从人工审核模式起步。
- 建立 patch sandbox、失败回退、build/test/race/profile/benchmark validator。
- 每个 rewrite 输出适用条件、已证明条件、未知条件、事件减少量和加速比。

### 14.6 阶段六：完整评价与论文收敛

- 按 RQ1–RQ7 完成 taxonomy、analysis、graph、rewrite 和 overhead 实验。
- 做 ablation，量化四项创新各自带来的精度、解释力或优化收益。
- 与相关 profiler、FFI/cgo analysis 和自动优化工作进行能力对比，谨慎验证 novelty claim。
- 将“当前原型能做什么”和“完整系统实现了什么”统一到同一版本、同一 commit 和同一 artifact 中。

## 15. 论文写作时可采用的章节结构

一个可能的论文结构如下：

1. Introduction
   - 多语言系统中的跨层性能问题。
   - Go/cgo 的实际使用与诊断困难。
   - 现有函数级 profiler 与静态 cgo checker 之间的能力空白。
   - 本文提出 CGOProf 和四项贡献。

2. Background and Motivation
   - cgo 调用机制。
   - Go/C representation、pointer passing、GC 和 callback contract。
   - motivating example：热点可见但成本根因和优化合法性不可见。

3. Empirical Characterization and Problem Formulation
   - cgo corpus 与人工研究方法。
   - cgo boundary inefficiency 的正式定义。
   - 三类五模式 taxonomy、方向标签与证据等级。

4. CGOProf Overview
   - 系统架构。
   - 静态与动态信息流。
   - 从 L0 finding 到 L2 rewrite 的流程。

5. Contract-Aware Semantic and Cost Analysis
   - ownership/lifetime/escape/callback/mutability summary。
   - boundary cost decomposition 与 calibration。
   - conservative unknown handling。

6. Ownership-Aware CGO Interaction Graph
   - 节点、边、context 和 object identity。
   - 五个基础 graph motif 与组合模式。
   - finding ranking 和 evidence generation。

7. Proof-Obligation-Guided Rewrite
   - rewrite template 与 legality condition。
   - L0/L1/L2 自动化边界。
   - validation、rollback 和 performance confirmation。

8. Implementation
   - Go AST rewriter、runtime recorder、Go/C analyzers 和 Python reporting。
   - handling nested calls、defer、callbacks、pointer/object summaries。
   - engineering choices and overhead control。

9. Evaluation
   - RQ1 taxonomy prevalence/coverage。
   - RQ2 contract accuracy 与 RQ3 cost attribution。
   - RQ4 graph accuracy 与 RQ5 diagnosis effectiveness/ablation。
   - RQ6 rewrite safety/speedup 与 RQ7 overhead。
   - real-world case studies and accepted patches（若获得）。

10. Discussion and Threats to Validity
   - workload dependence、incomplete C source 和 platform variation。
   - dynamic validation 与 semantic proof 的边界。
   - 与通用 profiler、compiler optimization 的互补关系。

11. Related Work
   - Python cross-layer profiling。
   - FFI performance analysis。
   - Go profiling、cgo correctness/performance analysis。
   - ownership/escape analysis、profile-guided optimization 和 verified rewrite。

12. Conclusion
   - 总结从问题定义、成本归因到安全优化的完整贡献。

## 16. 当前一句话总结

目标版本的 CGOProf 将 cgo 边界低效组织为三类五种语义模式，通过 contract-aware boundary-cost analysis 和 ownership-aware interaction graph 判断成本来源与可消除性，并仅对满足 proof obligations 的高收益 finding 生成和验证 rewrite，从而把“哪里慢”推进到“为什么慢、能否安全消除以及实际能加速多少”。

当前原型已经具备 AST 自动插桩、动态事件采集、site-level graph、五条基础 detector、真实项目 profile 和示例级优化 benchmark；contract inference、对象级图、严格成本分解和通用自动 rewrite 是下一阶段的核心实现任务。
