# CadBridge 产品需求说明

文档版本：1.0 · 日期：2026-09-17 · 阶段：架构交付，尚未实现产品。

本文件与 [DESIGN.md](DESIGN.md)、[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)、[ACCEPTANCE.md](ACCEPTANCE.md) 构成同一基线。原始需求为根目录《技术架构生成提示词.md》，该文件保持不变。本文的“必须”表示产品要求，不表示已经实现或实测通过。

## 1. 定位与成功定义

CadBridge 是 Windows 本机 AutoCAD 开发与自动化桥梁。AI Agent、开发者及测试程序能够连接用户明确选定的 AutoCAD，读取真实图元，实施受控修改，并加载、运行和断点调试 AutoLISP。

两个不可删减的核心目标：

1. **真实 CAD 感知**：结构化读取文档、Model/Layout、选择集、图元、几何、文字及块属性；截图补充而不替代数据库查询。
2. **真实 LSP 调试**：Breakpoint、Continue、Step Into/Over/Out、Watch、Locals、Evaluate、Call Stack、Break on Error；不得以重复执行、打印日志或模拟调用栈冒充调试器。

成功以目标 DWG 的实际状态、调试停止位置和独立读回值为准，不以工具返回 success 为准。失败不能静默改错图、重复写入或把未知结果报告为回滚成功。

## 2. 用户与关键流程

| 场景 | 闭环 |
|---|---|
| CAD 使用者 + AI | 选择运行实例 → 读取文档/图元 → 发现能力 → 提交修改 → 读回验证 → 按需截图 |
| LSP 开发者 + Coding Agent | 编辑源码 → 加载/重载 → 设置断点 → 运行 → 检查栈/变量 → 单步定位 → 检查图面 → 修复重测 |
| 自动化测试程序 | 临时启动指定 CAD 或附加明确 PID → 准备一次性测试图 → 测试 → 保存证据 → 断开，不擅自关闭用户 CAD |
| 维护人员 | doctor --json → 区分加载、身份、协议、宿主忙碌及调试器故障 → 明确处理 |

绿色流程：解压到可写目录，列出安装版本和运行实例，用户选择目标，Launcher 选择 DLL 并临时 NETLOAD，验证实例身份后连接 Host。重复连接复用同版插件，不在已加载旧 DLL 的进程中强行换版。

## 3. 已确定的架构边界

| 模块 | 产品决定 |
|---|---|
| M1 | C#/.NET Plugin；V1 仅 AutoCAD；COM/C++ 不作为核心 Runtime |
| M2 | Host ↔ Plugin 使用 Named Pipe；外部调用者不直接使用该协议 |
| M3 | 独立 C# Host；官方 MCP SDK、REST、薄 CLI、事件通道；共享服务 |
| M4 | 细粒度强类型 Registry + 7 常驻工具 + Search/Describe/Call + Batch |
| M5 | 单命令为单元素 Batch；数据库写默认 atomic；显式 best_effort；身份、Revision、幂等保护 |
| M6 | Lisp Runtime + 官方调试体系优先的 Debug Provider；断点调试是核心能力 |
| M7 | V1 不实现 Headless；预留 Backend，未来优先评估 CoreConsole |
| M8 | net48（2015–2024）、net8.0-windows（2025–2026）两个插件目标；共享源码、薄兼容层 |
| M9 | Portable First；按需临时加载；不默认 MSI/.bundle/永久自启动/全局信任路径修改 |

Host 目标框架与插件分开选择，允许使用当前受支持的现代 .NET LTS。

## 4. 支持目标、证据与风险

### 4.1 环境

V1 面向 Windows x64、完整 AutoCAD 2015–2026 和合法已安装宿主。AutoCAD LT、Mac/Linux、垂直产品专用对象、ZWCAD 不属于 V1 实现范围。预留条件不等于实现空壳。

绿色包可以带 Host 可再分发运行时；**不代表 AutoCAD、.NET Framework 4.8、厂商调试器也无需安装**。环境前提由 doctor 报告，不静默安装。

### 4.2 兼容矩阵

| 目标 | 插件计划 | 基础 CAD/Lisp | 官方 DAP 调试证据 | 本项目状态 |
|---|---|---|---|---|
| 2015–2020 | Legacy/net48 | **V1 必须支持：CAD Query / Edit / Batch / Lisp Runtime** | 官方 AutoLispExt 调试范围为 2021+；Debugger 仅作为 G02 探索项 | 基础能力未验证；Debugger 不作为 V1 放行条件 |
| 2021–2024 | Legacy/net48 | 目标支持 | 存在官方调试路径 | 未验证 |
| 2025 | Modern/net8.0-windows | 目标支持 | 官方范围内，仍须逐项实测 | 未验证 |
| 2026 ≤ Update 1.1 | Modern/net8.0-windows | 目标支持 | 同上 | 未验证 |
| 2026 ≥ Update 1.2 | 优先验证同一 Modern DLL | 官方列宿主 .NET 10，不能只按年份判断 | 同上 | 补丁级兼容待验证 |

证据见 DESIGN 的 S01–S05、S09。参考 csproj 证明共享源码构建方法存在，不证明 CadBridge 已跨版本通过。

**正式支持范围**：AutoCAD 2015–2026 的 CAD Query / Edit / Batch / Lisp Runtime 属于 V1 必须能力；AutoCAD 2021–2026 额外必须交付官方真实 Debugger（Breakpoint / Step / Watch / Call Stack 等）。AutoCAD 2015–2020 的 Debugger 仅作为 G02 探索项：应调查可行的受支持路径并记录证据，但未找到可行 Debug Provider **不阻塞 V1**，也不得用日志、重复执行或模拟调用栈冒充真调试。若未来获得可靠旧版 Debug Provider，可作为增强能力加入支持矩阵。

### 4.3 当前交付层级

本次为“设计就绪，可进入早期 PoC”的文档交付。所有构建、真机、DAP、性能和 Agent 验证初始为 NOT_RUN。关键 Gate 未过时，只能推进不依赖该假设的工作，不能宣布完整架构已被运行证据证明。

## 5. V1 功能需求

R01–R24 为跨文档唯一需求编号；设计见 DESIGN，阶段见 IMPLEMENTATION_PLAN，对应验收 A01–A24 见 ACCEPTANCE。

| 编号 | 必须交付的能力与边界 |
|---|---|
| R01 | 绿色目录集中保存程序、配置和数据；不默认永久加载，退出后可删除；AutoCAD/系统自有缓存不属于“零痕迹”承诺。 |
| R02 | 按明确 PID、启动时间、产品路径附加；仅显式 launch 才启动新 CAD。附加不确定必须失败，不另起隐藏实例。 |
| R03 | 多实例/文档身份、插件版本、协议协商、能力及连接状态可查询；不自动将失效目标替换成其他 CAD。 |
| R04 | 默认 7 工具：cad_status、cad_query、cad_view、search_tools、describe_tools、call_tool、cad_batch；lisp-dev 增加 lisp_runtime、debug_control、debug_inspect。中文发现、按需 Schema、权限过滤可测。 |
| R05 | MCP/REST/CLI 共用命令契约、校验与错误；帮助、发现和执行不能各维护参数表。Plugin 不解释自然语言。 |
| R06 | summary/entities/entity/selection/layers/blocks/text/region 真实查询；明确 Model/Layout；覆盖 LINE、ARC、CIRCLE、LWPOLYLINE、SPLINE、HATCH、INSERT、TEXT、MTEXT、DIMENSION；筛选/投影/分页/截断/未知属性显式。 |
| R07 | 当前视图截图及缩放；注明时间、文档/视图状态；不能把遮挡桌面或历史图像当作当前 CAD 渲染。 |
| R08 | 最小编辑集：线/圆/轻量多段线/文字/图层创建，移动/旋转/缩放/删除，块插入及块属性修改；高级命令不因可扩展而默认为 V1 已实现。 |
| R09 | 单文档 atomic 数据库 Batch：前检、步骤引用、整体提交/撤销；禁止任意 LSP、宿主命令、保存/导出等副作用混入。 |
| R10 | best_effort 独立步骤具有独立结果/提交边界；依赖失败标 skipped_dependency；部分成功不报告全成。 |
| R11 | Revision 覆盖 Bridge、用户/其他插件的受监测数据库修改、Undo/Redo；合法 CAD 上下文内再检身份和版本；Revision 不可信时拒绝保护性写入。 |
| R12 | 写入幂等键与状态查询：同键同载荷不重复，同键异载荷拒绝；响应丢失可恢复；证据不足报告 outcome_unknown，不宣称跨崩溃 exactly-once。 |
| R13 | LSP load/reload/eval/call/run_command、输出/错误/可得源码位置、测试循环；区分提交/开始/完成；明确中文路径编码、reload 非卸载、任意 LSP 无通用回滚。 |
| R14 | **AutoCAD 2021–2026** 真正 DAP 调试：明确 PID，Breakpoint、Continue、Step Into/Over/Out、Locals/Watch/Evaluate/Call Stack、Break on Error；框架 ID、源码版本、停止事件真实。AutoCAD 2015–2020 Debugger 为 G02 探索项，不作为 V1 必须能力。 |
| R15 | 在 **2021–2026 已支持 Debugger 的组合**中，暂停时调试控制面不被执行队列堵塞；数据库查询实时性与安全性须验证；缓存必须标陈旧，不能冒充暂停点实时图面。 |
| R16 | MCP HTTP、兼容 stdio shim、REST、CLI、SSE 共用认证与服务；提供任务/事件有限等待与恢复；SSE 不保证任意 Agent 自动唤醒。 |
| R17 | 长任务、排队、超时、取消、断线和 Host 重启具有明确状态；停止等待不等于 CAD 已停；不强杀用户 CAD，不盲重试写入。 |
| R18 | loopback 默认、Pipe 当前用户权限、凭据保护、路径策略和高风险授权；图纸文字/块属性是数据，不是指令。 |
| R19 | 两套 Shell 共享主体；逐版本/补丁能力矩阵；仅实测组合标 verified，不把编译成功当作全部年份保证。 |
| R20 | status/doctor、结构化日志、request_id、脱敏支持包；doctor 默认只读、不启动 CAD、不改配置、不联网。 |
| R21 | 官方 SDK/API 优先，开源来源/许可可追溯；SDK 适配层隔离、锁版本、测试后升级，不自动追 main/latest。 |
| R22 | 模块边界、契约版本、错误模型、依赖及并行开发合同明确；重大范围变化显式评审。 |
| R23 | 仅 LiveAutoCadBackend；未来 ZWCAD/CoreConsole 预留服务边界，不引入 V1 Headless 依赖或整套对象包装。 |
| R24 | 分层测试、真机矩阵及 Agent E2E；Token/延迟/正确性共同评价，保存输入输出、版本、构建标识和独立读回证据。 |

## 6. 非功能预算

以下是**验收目标，不是实测成绩**；不可在看过结果后未经评审抬高门槛。

| 维度 | V1 目标 |
|---|---|
| 目标正确性 | 错 PID、文档关闭重开、选择过期后写入不得落到另一目标 |
| 安全 | atomic 支持集合无部分提交；best_effort 如实报告；默认不保存、不覆盖 DWG |
| 常驻 Schema | 参考 tokenizer：default 7 工具 ≤ 6,000 tokens；lisp-dev 10 工具 ≤ 9,000；标明 tokenizer/序列化口径 |
| 扩张成本 | Registry 100/200/500 能力时常驻 Schema 变化 ≤ 5%；不得将全量命令枚举塞进通用 args Schema |
| 检索 | 独立中英文任务集 Top-3 ≥ 95%；无权限绕过；校验错误附字段路径/期望类型 |
| 默认结果 | query 默认 ≤ 100 项，文本 ≤ 64 KiB；超额分页/附件化并标 truncated |
| 交互 | 健康本地纯 Host status/search P95 ≤ 300 ms；CAD 空闲的小查询 P95 ≤ 2 s；忙碌单列，不混入达标值 |
| Batch | 同操作、读回与事务条件比较；支持一次外部调用/一次 Pipe 载荷；不预报固定加速倍数 |
| 维护 | 业务层不依赖 MCP SDK 类型；插件不承载 Web Server；生产操作不依赖 Python Runtime |
| 绿色部署 | 中文/空格路径可用；不可写/安全策略阻挡明确报错，不偷偷改系统设置 |

## 7. V1 不做什么

Headless/CoreConsole/APS/ezdxf 实现、ZWCAD 实现、云端直接连接本机、跨 DWG 原子事务、任意原生自定义对象编辑、Managed DLL 热卸载、远程无人值守、在线自动更新、完整 GUI、插件市场和全 CAD 命令覆盖不属于 V1。

V1 Launcher 用 CLI 承载，不新增 GUI 技术栈。永久加载未来可选。FAS/VLX/MNL 运行可后续评估，不承诺无源码断点调试；当前硬要求为有源码 `.lsp`。

## 8. 对前期说法的严格化

详细依据与决策见 DESIGN ADR：

- 支持年份拆成构建目标、运行验证、调试验证。
- “所有操作 Batch”指 CAD 业务载荷；握手/心跳/取消/DAP 控制不进入同一数据库事务。
- 原子回滚只承诺受支持的数据库操作，任意 LSP/文件写入不是通用事务。
- 绿色不等于不需要宿主/Framework，也不等于绕过 SECURELOAD。
- 7 工具需真实 Schema/任务评测，不沿用前期未核验的 Token 估算。
- 暂停时实时感知保留为验证要求，不以自动恢复或历史快照规避。

## 9. 交付分层

**文档交付**：四文档存在、可追踪、高风险有验证方案，可移交下一位 AI 从 P0/P1 开始；不要求本轮实现 PoC。

**阶段交付**：通过对应 Gate 并交付可复现证据。无实机记 BLOCKED，Fake Plugin 不代替真机。

**完整 V1**：R01–R24 完成实际验收；全部宣称支持的版本/补丁有证据。其中 2015–2020 必须完成 CAD Query / Edit / Batch / Lisp Runtime；2021–2026 还必须完成真实 Debugger 与暂停点调试闭环。2015–2020 Debugger 探索结果可以为 unavailable/unsupported-with-evidence，而不阻塞 V1。

阅读顺序：PRD → DESIGN → IMPLEMENTATION_PLAN → ACCEPTANCE。后续 AI 先检查风险 Gate，再领取模块，不从上百个工具铺开实现。
