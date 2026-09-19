# CadBridge 分阶段实施计划

版本：1.0 · 2026-09-17 · 基线：[PRD.md](PRD.md) / [DESIGN.md](DESIGN.md) / [ACCEPTANCE.md](ACCEPTANCE.md)。

**本文件描述后续实施，不表示阶段已执行。** 本轮仅生成文档。P0–P8、G01–G06 和产品验收初始全部 NOT_RUN；不得因为文档写全就将 Gate 改为 PASS。

## 1. 总体顺序与停点

```text
P0 环境/来源/合同基线
  -> P1 最小真实运行与调试可行性
       -> G01 + G02 强制评审
  -> P2 合同/目录/测试底座
  -> P3 数据库安全执行与查询
       -> G03 强制评审
  -> P4 MCP/REST/CLI/事件与发现
       -> G04 强制评审
  -> P5 Lisp Runtime 与正式调试整合
  -> P6 绿色部署/诊断/安全/视觉整合
       -> G05 强制评审
  -> P7 兼容矩阵/故障/Agent E2E
  -> P8 独立审核与发布
       -> G06 强制评审
```

P1 必须尽早做真实 DAP，而不是完成几个月的普通 CAD 工具后才发现 Debugger 无法接入。G02 未过不阻止独立的 Contracts/Fake 测试研究，但**不能将核心调试前提默认为成立并全面铺开交付**。

每个强制 Gate 形成证据包与评审结论后停下，向项目所有者汇报；未经明确批准不进入依赖该 Gate 的下一阶段。允许执行模型自主完成阶段内普通编辑/构建/测试，不逐条动作请示。付费资源、破坏性数据操作、提权、永久配置或范围降级必须事先确认。

## 2. 多 AI 协作合同

一次只交给执行 AI 一个里程碑或明确任务包，不给“实现全部功能直到完成”的模糊目标。开始前读四文档、当前 Gate 状态、实际工作树，不能依赖前一 AI 的成功汇报。

每个任务包必须保留：需求 Rxx、负责目录/接口、输入输出合同版本、测试方法、实际执行命令与退出码、证据路径、未解决问题。实现改变公共 Schema、状态/错误枚举、线程约束或支持范围时，先更新设计变更记录和影响矩阵，不在业务模块里私自加另一套。

并行执行采用独立分支/worktree或明确互斥目录；未经过集成者确认不同时改 Contracts/Registry。未经授权不 reset/clean/stash 用户修改，不以重建仓库掩盖冲突。提交采用阶段小提交，是否初始化 Git/提交由实施阶段按现场状态决定，本次文档生成不擅自建立仓库。

建议所有者：

| 工作包 | 负责边界 | 依赖输出 |
|---|---|---|
| Contract/Registry | DTO、Schema、错误、能力版本、Fixture | 所有人只消费该基线 |
| AutoCAD Runtime | Shell、主线程调度、事务、事件、图元查询 | Pipe业务合同与读回证据 |
| Host/Transport | Pipe client、Job、REST/CLI、权限 | 不实现 CAD API |
| MCP/Discovery | 官方 SDK Adapter、7/10工具、检索/描述 | Registry不另复制 |
| Lisp/Debugger | LispService、DAP Provider、暂停协调 | 不绕过目标/权限/Job |
| QA/Release | 黑盒测试、故障注入、证据复核 | 与实现者分离阅读证据 |

## 3. P0 — 环境、来源与实施基线

**覆盖：R19、R21、R22、R23。输入：四文档和实际工作目录。**

任务：

- T00-1：只读盘点 Windows、.NET SDK/Framework、已安装完整 AutoCAD 年份/补丁/语言、可用测试许可、现有 CAD 会话。不得为盘点启动或关闭用户 CAD。
- T00-2：锁定官方 MCP SDK、Host LTS、Schema/CLI/日志/本地检索/DAP候选库。读取实际版本/许可证/依赖；记录选择理由及不采用项。优先标准库/官方库，不自动引入 Python/Node生产进程。
- T00-3：锁定 DESIGN Sxx 的关键来源到 tag/commit/file hash；复用词典/代码逐项登记 LICENSE/NOTICE。厂商二进制不因扩展开源而进入包。
- T00-4：建立后续代码目录建议、测试框架和证据布局；若当前工作树非空保留并审查。合同 JSON 示例与命令清单仅在一致性评审后转为机器可读定义。
- T00-5：确定真机矩阵的实际可用组合。缺失 CAD 版本记 BLOCKED，不把未安装版本标 unsupported，也不假称“可编译即支持”。

产物：环境/依赖/来源锁定记录、兼容矩阵初稿、选库记录、风险负责人、P1测试计划；可放后续 `docs/evidence/P0/`。验证：所有选择有来源，source和binary许可分开；M7无额外Runtime依赖。

退出：P1具备至少一个Legacy、一个Modern真机环境和官方调试范围内的宿主；缺失者清楚标阻塞。若不能具备，先完成可执行测试方案，不伪造运行记录。

## 4. P1 — 最小真实链路与高风险 PoC

**覆盖：R02、R03、R13–R15、R19。输入：P0环境。**

这是实施时允许写小规模可丢弃 PoC 的阶段；不是本轮文档工作。PoC只在空白/副本测试图运行，不触碰生产 DWG。

### T01-1：Legacy/Modern最小插件与调度

两 csproj共享源码；按 S09 思路引用最低目标API，不复制宿主DLL。实现最小ping/document.info/query +受控创建一个圆；证明Pipe收发后台、CAD API合法上下文、DocumentLock/Transaction边界。记录实际.NET/AutoCAD版本，不只打印自定义version常量。

### T01-2：绿色 Bootstrap 与准确目标

列举已打开实例；指定PID→COM对象HWND/PID核验→NETLOAD→Plugin identity读回。必须测试同版本两个实例、版本不符、权限不符、SECURELOAD阻挡、未启动时显式launch及重复attach。加载失败不得偷偷Dispatch新实例。

### T01-3：官方 DAP 独立 Client

从所选CAD安装目录定位适配器；不使用VS Code UI代替证明。对有源码fixture真实完成attach、设断点、异常策略、运行、stopped、stack/scopes/variables、step/continue。保存DAP收发顺序与关键事件，移除凭据。检验Adapter错误扩展和源码hash。

### T01-4：LSP结果、中文与交互

证明load/eval/call/run_command可区分排队和完成、返回真实值、捕获可支持输出及异常。测试中文/空格路径与MBCS/Unicode组合、不支持字符拒绝、reload残留定义、getpoint/DCL等待状态、客户端timeout不假称CAD停止。

### T01-5：暂停点图面与防死锁

让LSP在创建一个实体之后、修改之前停住；尝试由Plugin合法读取该实体，验证Debugger仍可step/continue。检测主线程队列堵塞、未提交状态读取、重入和陈旧快照。不能用“先continue再query”通过此项。

### T01-6：版本边界

官方DAP声明2021+：**2021–2026 真 Debugger 是 V1 强制能力**；2015–2020 仅探索是否存在受支持调试路径，对每一候选路线给证据/许可/依赖/局限。未找到旧版 Debug Provider 时记录 unavailable/unsupported-with-evidence，不阻塞 2015–2020 的 Query/Edit/Batch/Lisp Runtime。Modern 对 2026 Update1.2 前后单列；只要同 DLL 未测，就保留未验证，不凭 net8 标签通过。

**G01出口**：A01–A03/A19 的PoC子集通过，准确附加+两Shell+Pipe最小读写有真机证据。

**G02出口**：A13 的 Lisp Runtime 子集按目标版本验证；A14–A15 对 **2021–2026** 的 PoC 子集必须通过，特别是无 VS Code 真实调试和暂停查询。2015–2020 Debugger 只要求完成探索、证据和 capability 结论，无法兑现不会让 G02 因此 BLOCKED；不擅自用日志重跑或现代宿主冒充旧版实时调试。

G01/G02通过后冻结可实现的技术合同，再交下一批AI开展主体。PoC成功不等于生产实现完成；代码是否保留必须复核安全/错误/资源释放。

## 5. P2 — Contracts、Registry与测试底座

**覆盖：R04、R05、R16、R18、R22、R23。输入：G01/G02结论，或经批准的独立合同工作。**

- T02-1：Contracts/Schema/错误/身份/Batch/Result、Canonical payload hash及步骤引用规则；生成机器可读操作目录和来源标识。
- T02-2：Registry→帮助/MCP入口/REST/CLI映射；七工具与lisp-dev三工具的固定profile，禁止全量oneOf膨胀。
- T02-3：Fake Plugin、Fake DAP、测试时钟、帧拆包/粘包/超长/EOF/重复key fixture、权限与会话fixture。
- T02-4：Host骨架、目标选择、统一认证和Job状态；外部Adapter暂只接Fake后端。
- T02-5：定义Live Backend接口，测试中fake backend仅为替身；不实现未来Headless或一堆ICadEntity类。

产物：公共合同与金样、机器目录、Fake transports、可跑的纯测试和合同测试。验证A04/A05/A16/A18/A22/A23相应子集。所有改变合同的任务由同一所有者合并。

## 6. P3 — 数据库安全执行与图元感知

**覆盖：R06、R08–R12、R17。输入：P2合同、G01/G02放行。**

- T03-1：Plugin正式调度器，串行CAD上下文、状态检查、bounded queue；控制消息不能排在Lisp长任务之后。
- T03-2：document_id/Revision的外部事件覆盖，dirty排空、Undo/Redo、排队竞态、untrusted恢复；只统计自身修改不通过。
- T03-3：atomic/best_effort执行器、前检与步骤绑定、后置读回、失败/回滚/skipped报告；Handler不能自行Commit。
- T03-4：Plugin级幂等登记、Host job记录、重复写/响应丢失/Host重启恢复；跨CAD崩溃unknown及保护性停止。
- T03-5：R08最小编辑集。一次接入一类操作，同时补正反测试，不先铺满未来命令。
- T03-6：R06查询，Model/Layout、块实例与变换、文本/属性、bbox候选范围、不可支持字段、revision绑定cursor、输出budget。

**G03出口**：A06/A08–A12/A17安全执行部分通过；特别是外部修改冲突、atomic无部分效果、best_effort如实结果、幂等不重复。证据需独立查询或重新打开图验证，不只检查Handler自己返回的坐标。

在G03前，Host写工具不对普通Agent开放；允许隔离测试环境中的受控写入。

## 7. P4 — MCP、REST、CLI与Discovery

**覆盖：R04、R05、R16、R18、R21、R24。输入：P2；写入部分依赖G03。**

- T04-1：官方SDKHTTP与stdio shim，至少两种真实客户端；兼容状态不交给手写initialize分支。
- T04-2：中英文发现语料/成熟本地检索、短签名、完整describe、错误签名回传；每条导入词典映射到实际实现能力。
- T04-3：真实权限/Capability过滤及调用时再次校验，batch/meta recursion绕过负例。
- T04-4：REST统一execute/batch，CLI所有CAD行为经Host；离线doctor只读不需要Host。
- T04-5：SSE、events.wait、Job查询/取消与断线恢复；消费不了SSE的Agent有有界等待路径。
- T04-6：真实tools/list tokenize；100/200/500目录；中英文独立检索集；全任务输入输出与重试/缓存成本对比。

**G04出口**：7/10入口、Schema契约、权限、客户端连接、PRD Token与Top-3预算通过。若某客户端缓存导致无收益，如实报告；不将第三方项目估算当本项目测量。

## 8. P5 — 正式Lisp与Debugger

**覆盖：R13–R15、R17。输入：G02、P2、P3查询/状态、P4事件。**

- T05-1：将PoC收敛为正式LispService；源码hash/编码、作业状态、受控输出/异常、reload边界。
- T05-2：DAP会话/stop_id/断点源码版本/Watch/Locals/Stack/Step/Break on Error；厂商扩展隔离。
- T05-3：暂停状态的安全query与截图证据；如果PoC确定限制，执行已批准的范围决定，不自行伪装live。
- T05-4：权限/控制租约、多Agent冲突、附加到错误PID、step后失效frame、文件修改后失效断点；Evaluate的副作用授权。
- T05-5：运行源码fixture及故障定位闭环；测试不能只验证DAP模拟器收到请求。

产物：正式模块、源码fixture、真实DAP transcript、暂停点图面/变量证据、全部支持组合的功能表。A13–A15/A17实际产品级测试通过才允许称Lisp调试完成。

## 9. P6 — 绿色包、诊断、安全和视觉整合

**覆盖：R01–R03、R07、R16–R21。输入：P3–P5。**

- T06-1：Portable目录、Launcher CLI、Host单实例/生命周期、多root/多CAD冲突、离线换版/旧DLL存活提示。
- T06-2：日志/脱敏支持包、doctor细分类、认证endpoint descriptor、DPAPI/ACL、目录不可写和跨机器secret失效。
- T06-3：view捕获及附件读取、遮挡/最小化行为、范围/hash/revision信息；不把文件路径当图像交付。
- T06-4：安全负例：不可信Origin、跨用户Pipe、伪造descriptor、路径越界/reparse/覆盖、伪造Schema/权限、图纸提示注入。
- T06-5：原图保护、断开不关用户CAD、不设置SECURELOAD=0、不改LISPSYS/永久自动加载；记录受策略阻挡时的实际流程。

**G05出口**：一个完整绿色包可在授权真机环境，从解压到临时附加、查询、修改、调试、诊断、断开全部贯通；A01–A03/A07/A16–A21集成子集通过。

## 10. P7 — 兼容、压力、故障与Agent E2E

**覆盖：R01–R24。输入：G05。**

- T07-1：至少2015、2024、2025、2026代表版本；2026已有不同宿主Runtime补丁必须各测。2015–2026 均验证 Query/Edit/Batch/Lisp Runtime；Debugger 正式矩阵只覆盖 2021–2026，2015–2020 保留探索结果。计划宣称全2015–2026基础能力支持时，各年/发布配置均需smoke，代表样本不能代替完整宣称。
- T07-2：长会话、100步批、队列满、低磁盘、客户端断线、Host重启、CAD意外结束、stale cursor/stop_id、未知结果下安全锁止。
- T07-3：真实Agent任务：按条件查询/修改/读回图元；通过真Debugger定位并修复故障LSP。保留Agent版本、模型、客户端、tokenizer、输入输出及独立验证。
- T07-4：冷/热连接、忙/闲宿主分开采样；Token常驻/结果/任务总量与延迟都报告，不捏造单一“效率星级”。

产物：完整证据manifest和支持矩阵。没有环境/许可的组合为BLOCKED；不得把skip当PASS。

## 11. P8 — 独立审核与发布

**输入：P7证据。** 独立审核者重新读取需求与核心实现，复现关键负例，不能只读实现AI总结。检查source pin/license、包内没有宿主私有DLL、配置无凭据、文档与实际CLI一致。

**G06出口**：ACCEPTANCE全部要求的测试状态、支持矩阵和用户批准的范围变更齐全；无未处置阻断项。交付绿色包、对应四文档版本、证据目录、hash清单与已知限制。

审核FAIL则开定向修复任务→重测受影响层→重新审核；禁止删除失败测试、改门槛或将Mock转绿后宣称真机通过。完整V1和“仅PoC/限定试用包”必须分别命名。

## 12. 可并行与不可并行

| 可并行的工作 | 前提 |
|---|---|
| P1运行加载PoC、DAP源码调研、依赖许可证核验 | 使用独立fixture；不竞争同一CAD会话 |
| P2 Fake transports与检索数据准备 | Contracts版本已固定 |
| P3纯查询实现与P4基于Fake的MCP接入 | 不修改共同Schema；真实写入待G03 |
| P4 CLI/REST与检索Benchmark | Registry/Profile合同一致 |
| P6诊断文案/包布局与P5单模块完善 | 不提前宣布集成通过 |

不可并行：同一acad.exe里的两个写测试；使用同一DAP session的两位控制者；多AI同时编辑Contracts；在断点期间另一个任务切图/更新DLL/关闭Host。

## 13. 需求追踪矩阵

| 需求 | DESIGN承载 | 实施任务/阶段 | 验收 |
|---|---|---|---|
| R01 | D13 | T06-1/T06-5 | A01 |
| R02 | D06/D13 | T01-2/T06-1 | A02 |
| R03 | D06/D07 | T01-1/T02-4/T06-1 | A03 |
| R04 | D03/D04 | T02-2/T04-1/T04-2/T04-6 | A04 |
| R05 | D03/D05/D08 | T02-1/T02-2/T04-4 | A05 |
| R06 | D10 | T03-6 | A06 |
| R07 | D10/D14 | T06-3 | A07 |
| R08 | D03/D09 | T03-5 | A08 |
| R09 | D08/D09 | T03-3 | A09 |
| R10 | D09 | T03-3 | A10 |
| R11 | D06/D09 | T03-2 | A11 |
| R12 | D09 | T03-4 | A12 |
| R13 | D11 | T01-4/T05-1 | A13 |
| R14 | D12 | T01-3/T01-6/T05-2 | A14 |
| R15 | D07/D10/D12 | T01-5/T05-3 | A15 |
| R16 | D05/D06 | T02-4/T04-1/T04-4/T04-5 | A16 |
| R17 | D07/D09/D11/D12 | T03-1/T03-4/T05-4/T07-2 | A17 |
| R18 | D04/D14 | T04-3/T06-2/T06-4 | A18 |
| R19 | D02/D15 | T00-1/T00-5/T01-6/T07-1 | A19 |
| R20 | D14 | T06-2 | A20 |
| R21 | D15/D17 | T00-2/T00-3/P8 | A21 |
| R22 | D01–D18 | P0/P2/P8 | A22 |
| R23 | D01/D15 | T02-5 | A23 |
| R24 | D04/D16 | T04-6/T07-2/T07-3/T07-4/P8 | A24 |

## 14. 阶段交接格式

每次阶段结束至少报告：本次修改与公共合同版本；已执行测试及证据；NOT_RUN/BLOCKED/FAIL项；对应Gate；影响范围；下一位AI可开始的任务。不得写“所有Gate都通过”却不列清单。

建议证据路径 `docs/evidence/<phase>/<run_id>/`，含 `manifest.json`、命令/退出码、脱敏请求响应、构建hash、AutoCAD完整版本、fixture hash、独立读回。大DWG/录屏按用户授权保存，不默认提交到远程。

**首个交给执行AI的任务应是P0与P1计划内验证，不是一次性实现M1–M9。** 本文阶段产物路径是未来实施约定，本次并未创建或执行这些产物。
