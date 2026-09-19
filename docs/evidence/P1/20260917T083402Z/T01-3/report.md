# P1 / T01-3 — 官方 DAP 独立 Client：阶段结论

**结论：T01-3 部分通过（PARTIAL）。**
在 AutoCAD 2026 上，无 VS Code、无 DAP 模拟器，直接与 Autodesk 官方
`AutoLispDebugAdapter.exe` 交互，对**真实运行中的 GUI 进程**完成了
attach → 断点 → 停止 → 栈 → 变量 → 单步 → 继续（23/23 检查）。

**这不等于 T01-3 全部通过，也不使 G02 通过。** 计划中的异常策略（`runtimeerror`）、
错误 PID / 多实例行为、源码 hash 校验等子项**仍未验证**（见 §3）。
2015–2020 的调试探索亦未开始（T01-6）。

本报告同时纠正一个**早前由探针缺陷造成的错误结论**（见 §4），
并按审查意见**收窄了若干过强表述**（见 §4.2）。

---

## 1. 环境与源码基线

| 项 | 值 |
|---|---|
| 适配器 | `D:\Program Files\Autodesk\AutoCAD 2026\AutoLispDebugAdapter.exe` |
| 宿主 | `D:\Program Files\Autodesk\AutoCAD 2026\acad.exe`（真实 GUI 进程，非 headless） |
| fixture | `F:\CadBridge-run\20260917T083402Z\cb_debug_fixture.lsp`，208 字节，UTF-8/LF，sha256 `3123712cdcc47881b8ccce96ea88dc4218e7836e89ac37c3560412551f24d89e` |
| 客户端 | `tools/dap-probe.py`（DapClient）+ `tools/dap-attach-a14.py` |
| 扩展源码 | `Autodesk-AutoCAD/AutoLispExt` @ `74f59ee220a3`，`extension/src/debug.ts` blob `33f37992fe82aa4f07ae61f107b232d6f535a7de`、`extension/src/platform.ts` blob `3be5ead26ecb55f028548507ec102fe71a3e13bd` —— **与 P0 source-register.json 记录逐字节一致** |

### 1.1 两种拓扑必须区分（源码依据）

| 拓扑 | 适配器启动方式 | 会话请求 | 适配器是否拥有宿主 |
|---|---|---|---|
| launch | `adapter -- <acad.exe> [params]` | `launch {program}` | **是**（适配器自己拉起 CAD） |
| attach | `adapter`（**无参数**） | `attach {type:'attachlisp', request:'attach', processId:'<字符串>', program}` | 否（客户端已启动宿主） |

源码逐字依据：

```
class LaunchDebugAdapterExecutableFactory:
    let args = ["--", productStartCommand, productStartParameter];
    return new vscode.DebugAdapterExecutable(lispadapterpath, args);

class AttachDebugAdapterExecutableFactory:
    return new vscode.DebugAdapterExecutable(lispadapterpath);      // 无参数

class LispAttachConfigurationProvider:
    newConfig.type      = 'attachlisp';
    newConfig.request   = 'attach';
    newConfig.program   = <active editor file>;
    newConfig.processId = processId;      // acadPicker.pickProcess(): Promise<string | null>
```

**CadBridge 应走 attach 拓扑**：CadBridge 不应是拉起用户 CAD 的那一方。
（launch 拓扑也已验证可用，但它要求由适配器持有宿主进程，不适合本项目的约束。）

---

## 2. 已证明（attach 拓扑，23/23）

原始 transcript：`attach-a14.jsonl`；结构化结果：`attach-a14.summary.json`。

| # | 检查 | 实测结果 |
|---|---|---|
| 1 | `initialize` | success；capabilities `{supportsConfigurationDoneRequest: true, supportsEvaluateForHovers: true}` |
| 2 | `initialized` 事件 | 收到 |
| 3 | **`attach` 到活体 PID** | **success**（`processId` 为字符串 `'76296'`） |
| 4 | `setBreakpoints` 第 6、8 行 | success |
| 5 | 两个断点均 `verified` | `[{line:6, verified:true}, {line:8, verified:true}]` |
| 6 | 断点行号与请求一致 | 6→6，8→8（适配器自行回填 column 3 / endColumn 3） |
| 7 | `configurationDone` | success |
| 8 | `evaluate (C:CBDBG)` | success，`body.result=""` |
| 9 | **`stopped` 事件** | `{reason:'breakpoint', threadId:1}` |
| 10 | `stackTrace` 真实源帧 | `C:CBDBG` @ line 6，`source.path` 指向 fixture 真实路径 |
| 11 | stop1 局部变量 | `{N:'2', P:'nil'}` → **n=2** |
| 12 | `stepIn` ×2 | 第一次停在 `:BEFORE-EXP`，第二次进入 `CB-ADD` |
| 13 | 进入 `cb-add` 后 | line 1，局部变量 `{X:'2', Y:'nil'}` → **x=2** |
| 14 | `next` | 停在 line 3，局部变量 `{X:'2', Y:'3'}` → **y=3**（第 2 行已执行） |
| 15 | `stepOut` | 返回 `C:CBDBG` |
| 16 | `continue` | 命中第 8 行断点（line 8） |

### 2.1 与 launch 拓扑的一致性

launch 拓扑（`tools/dap-a14-sequence.py`，27/28）与 attach 拓扑得到**相同的语义结论**：
断点行号一致、栈帧名一致、局部变量名与值一致（`N/P`、`X/Y`）。
两者在**断点、栈、变量语义**上互相印证，说明这些语义不依赖适配器的启动方式。

**注意（本节的限度）**：两种拓扑共享同一 fixture、同一适配器二进制与同一宿主版本，
因此这不是两次完全独立的实验；它只支持「语义一致」，不支持更强的普适性结论。

### 2.2 A14 语义逐条对应

| ACCEPTANCE A14 要求 | 本报告证据 |
|---|---|
| 源码 fixture 上设断点并命中 | #4/#5/#9 |
| Step Into 进入被调函数 | #12/#13（`CB-ADD`，x=2） |
| 单步后局部变量反映真实求值 | #14（y=3，由 `(+ x 1)` 得出） |
| Step Out 返回调用者 | #15 |
| 第二个断点命中 | #16（line 8） |
| 全部数据来自真实 stop，非推断 | 每项均由 `stackTrace`/`scopes`/`variables` 现场读取（attach 拓扑） |

**该表只覆盖 A14 中被实测的那几项。** A14 还要求异常策略（`runtimeerror`）与
源码版本/hash 关联，本轮**未覆盖**（见 §3）。

---

## 3. 尚未覆盖（本项不声称已通过）

| 项 | 状态 | 说明 |
|---|---|---|
| `runtimeerror` 厂商事件 | NOT_OBSERVED | 需构造真实 Lisp 运行时错误 |
| `evaluate` 副作用授权语义 | NOT_OBSERVED | T01-4 范围 |
| 无效 PID 的错误形状 | NOT_OBSERVED | 需单独负例（本轮只测了合法 PID） |
| 多实例/错误 PID 附加 | NOT_OBSERVED | T01-2b 范围 |
| 源码 hash 校验 | NOT_OBSERVED | 适配器是否回传源码 hash 未验证 |
| 2015–2020 调试路径 | NOT_RUN | T01-6 范围 |
| 暂停点图面 live query | NOT_RUN | T01-5 范围（核心不可降级项） |

**本项结论不覆盖上述内容**，不得据 T01-3 PASS 推断 T01-4/T01-5/T01-6 已通过。

---

## 4. 重要纠正：早前“attach 无响应”是探针缺陷，不是产品限制

早前版本的本报告把 `attach` 判为“无响应”，并据此列出 5 个未证实假设（H-a…H-e）。
**该结论错误。** 根因全部在探针侧，共 3 处：

| # | 缺陷 | 后果 | 修正 | 必要性是否已单独证明 |
|---|---|---|---|---|
| 1 | `processId` 传**整数** | 适配器不接受该形态 | 传**字符串**（`pickProcess` 返回 `Promise<string\|null>`） | **否** |
| 2 | `type` 传 `'autolisp'` | 非 attach 配置类型 | 传 `'attachlisp'` | **否** |
| 3 | 响应超时过短 | 握手未完成即放弃 | 超时提高到 30s | **否** |

**这三项是同时改动的，因此无法从本轮结果断定哪一项（或哪几项）是必需的。**
若要断言「`processId` 必须是字符串」这类结论，需要一次只改一项的对照实验。

另有 2 处**工具链**缺陷，均在 `tools/dap-probe.py` 中已修复并保留注释：

| # | 缺陷 | 说明 |
|---|---|---|
| 4 | DAP 分帧解析器遇到头前的游离 LF 即 `return` | 使 reader 永久失聪，`initialize` 之后的一切都被误判为“适配器不响应” |
| 5 | `responses` 为按 `request_seq` 索引的 dict，被当作 list 使用 | 取响应时抛异常/取错 |

**并发的错误归因**：attach 失败时适配器会发出 `dgbfatalerr` 事件，
内容为 **“此 AutoCAD 实例当前用于调试其他文件”**（UTF-8 中文，已逐字节解码）。
这条消息证明适配器**确实在处理 attach**，只是当时同一 CAD 实例已被先前的尝试占用。
早前未解码该事件，因而错过了最直接的线索。

### 4.1 关于 H-e（真正的服务端是进程内 `vl_u.crx`）

P0 的静态发现（完整 DAP 请求词汇位于 `vl_u.crx`，而 `AutoLispDebugAdapter.exe` 只含握手）
**依然是静态发现，未被本轮动态实验证实或证伪**。它**不解释**本轮观测到的行为，
只是与之不矛盾。会话实际建立在哪里，本轮没有测量。

---

## 5. 产物

| 文件 | 内容 |
|---|---|
| `attach-a14.jsonl` / `attach-a14.summary.json` | **主证据**：attach 拓扑完整会话（23/23） |
| `a14-sequence.jsonl` / `.summary.json` | launch 拓扑对照（27/28） |
| `session-full.jsonl` / `.summary.json` | 早期 launch 会话（栈/作用域/变量首证） |
| `probe-launchform.jsonl` | launch 形态首证 |
| `probe-attach-live.jsonl`、`probe-badpid.jsonl`、`probe-bare.jsonl` | 早前失败的尝试（保留作缺陷复现记录） |
| `attach-matrix.json` + `attach-matrix/` | type/PID 形态矩阵（含 `dgbfatalerr` 原文） |
| `attach-ordering.json` + `attach-ordering/` | 顺序假设验证：attach 会响应（此前被误判为无响应）。**具体致因未单独证明**（多项同时改动） |
| `fixture-identity.json` | fixture 字节数/sha256/编码/行尾 |

**一个未解释的观察（非结论）**：某次运行中 stepOut 之后 `P` 读作 `nil`，而稍后在第 8 行
读作 `3`。这与「停止发生在包含它的 `setq` 完成之前」一致，**但没有被单独验证**；
**不应**据此断言帧缓存陈旧或适配器有缺陷。

工具：`tools/dap-probe.py`（客户端与分帧）、`tools/dap-a14-sequence.py`（launch 拓扑）、
`tools/dap-attach-a14.py`（attach 拓扑，主）、`tools/dap-attach-matrix.py`、
`tools/dap-attach-ordering.py`。

---

## 6. 未决

1. `evaluate` 在 attach 模式下的副作用边界（→ T01-4）。
2. 暂停点由**插件**（而非 DAP `evaluate`）合法读取图面实体（→ T01-5，核心项）。
3. 适配器对错误 PID / 多实例的行为（→ T01-2b）。
4. 2022 / 2024 宿主上的 attach 复现（→ T01-6）。
5. 许可证：本机所有 AutoCAD 宿主 `license_provenance=unverified`，
   本轮结果须标记 `LICENSE_UNVERIFIED`，在所有者确认前不得作为 Gate 放行证据。

---

## 7. 安全与现场

- **见 `SAFETY-INCIDENT-process-cleanup.md`**：早期清理使用按进程名终止（`Stop-Process -Force`），
  方式不当，已自纠。现改为 `safe_process.terminate_owned`（PID + 创建时间 + 可执行路径三重校验）。
- 启动前若发现同安装目录已有 `acad.exe` 则直接拒绝运行。
- 设置了 `ACAD_USERPROFILE` 指向 `F:\CadBridge-run\_attach-scratch\`；
  **该变量的隔离效果未经证实**，仅记录为「已设置」，不作为隔离保证。
- 未修改注册表、未改 `SECURELOAD`/`TRUSTEDPATHS`/`LISPSYS`、未写任何用户 DWG。
- fixture 位于 `F:\CadBridge-run\`（Nextcloud 同步根之外）。
