# P1 阶段报告 — 最小真实链路与高风险 PoC

**Run**：`20260917T083402Z`
**阶段状态**：**INCOMPLETE**
**Gate**：**G01 = NOT_RUN，G02 = NOT_RUN**
**阻塞**：`BLOCKED_SAFETY_REVIEW`（安全 harness 未通过审查 → 暂停新的真机实验）

---

## 1. 阶段目标回顾

P1 的作用是**在投入主体实现之前**证伪/证实关键技术前提：两 Shell 能否在真机加载执行、
能否准确附加到指定实例、**无 VS Code 的真实 DAP 调试**是否可用、暂停点能否合法读取图面。
计划的意图很明确：**宁可在这里失败，也不要几个月后才发现 Debugger 接不进去**。

## 2. 结论总览

| 项 | 状态 | 一句话结论 |
|---|---|---|
| T01-1 加载/事务/写入/跨进程回读 | **PASS** | 两 Shell 在真机内核与 GUI 均加载执行；写入由**独立进程**重新打开 DWG 读回验证 |
| T01-1f Pipe 与正式调度 | NOT_RUN | 未实现（P3 范围） |
| T01-2 隔离启动 | **PASS** | 只启动并只终止自己的实例 |
| T01-2b 目标选择/ATTACH_AMBIGUOUS | NOT_RUN | 未实现 |
| T01-3 无 VS Code 真实 DAP | **NOT_RUN** | **有实测子集**（2026 上 attach+断点+单步+栈+变量 23/23），但异常策略、错误 PID/多实例、源码 hash 未覆盖 |
| T01-4 LSP 结果/中文/交互 | NOT_RUN | 未执行 |
| T01-5 暂停点 live query | **NOT_RUN** | **核心机制有可行性证据**，但策略层未实现、且证据限度未覆盖 stop 世代/重入 |
| T01-6 版本边界 | NOT_RUN | 未执行 |

**G01/G02 均不得通过。**

## 3. 本阶段最重要的三个发现

### 3.1 官方 DAP 可用，且**必须走 attach 拓扑**

`AutoLispDebugAdapter.exe` 是真 DAP 服务端。CadBridge 需要的形态是：

```
裸启动 adapter  →  initialize  →  attach {type:'attachlisp',
                                          request:'attach',
                                          processId:'<字符串 PID>',
                                          program:'<绝对 .lsp>'}
```

随后 `setBreakpoints`（返回 `verified:true`）、`configurationDone`、`stopped`、
`stackTrace`/`scopes`/`variables`、`stepIn`/`next`/`stepOut`/`continue` 全部可用。

**早前「attach 无响应」的结论已被推翻**：修正后 attach 成功。
**但具体原因未被单独证明**——`processId` 改为字符串、`type` 改为 `attachlisp`、延长超时
这三项是**同时**改动的；分帧解析器的缺陷也同时存在。**各自必要性均未单独验证。**

### 3.2 暂停期插件读取**可行**，且通道反直觉

| 通道 | 结果 |
|---|---|
| COM | 观测：暂停期读取 35–45s 未返回；continue 后立即成功。这是**关联观测**，**不**证明所有插件路径都不可能 |
| DAP `hover` + **本插件函数** | 本函数在该上下文被产品拒绝：**「无法在监视窗口中计算用户定义的函数」**（内置函数如 `entget` 可用） |
| DAP `repl` + **插件 `[LispFunction]`** | 已观测可用；值经 **DAP `output` 事件**返回（本机观测 `repl` 的 `result` 为空） |

**限度**：`hover` 的拒绝只测了**该函数**，不得推广为「所有上下文」或「所有用户函数」；
`watch`/`clipboard` 对用户函数的行为**未测**；`repl` 的 `result` 为空是**本机观测**，非普遍保证。
`repl` 作为插件通道是**本机已验证的形态**，但**不声称是唯一可能的通道**。

### 3.3 写入授权必须由 CadBridge 自己强制

原始适配器在暂停期**不拒绝**写入（实测 `entmakex` 生效）。
因此 `DEBUG_BUSY`、`STALE_DEBUG_HANDLE`、控制租约都是 **Host 侧策略**，
不是适配器能力。这是 P2 合同与 P3 调度器的硬输入。

## 4. 阻塞与未决

### 4.1 安全（最高优先，已暂停真机实验）

- **事件**：早期清理使用按进程名终止（`Stop-Process -Force`），可能关闭用户 CAD。
  **实际影响无法事后确证**，故不声称「未造成损害」。
- **整改**：`tools/safe_process.py` 重写为可证明**归属**（只认 Popen 返回的 PID、
  身份字段全必需、记录带 launch nonce、优先用保留句柄终止），18 项注入式自检通过；
  `tools/selftest.sh` 增加 5 类静态检查并已负向验证。
- **仍未通过审查**（第二轮）：归属模型已改为**私有注册表 token**（调用方拼装的记录一律无效、
  终止只走保留句柄、**无 PID 兜底**），28 项注入式自检通过；`selftest.sh` 增加 6 类静态检查
  （含「COM 现场实验必须被安全闸门拦住」与「闸门默认拒绝」的负向验证）。
- **仍未完成**：COM 探针需改**有界工作进程**并强制 `HWND→自有 PID` 校验；
  插件回调上下文需对照 Autodesk **受支持文档**核实；需补**空闲正对照**；
  `disposed=true` 需如实反映每次释放结果。
- **现场实验已实际停用**：所有 COM 现场实验脚本现被 `require_safety_review_passed` 拦截，
  默认抛错且**不启动任何 CAD**（证据：`tooling/safety-gate-refusal.txt`）。

**第三方审计（2026-09-18）更新**：上述离线整改项已有进一步收敛，但 Live Safety Gate
仍为 **BLOCKED**，详见 `THIRD-AUDIT-LIVE-SAFETY.md`。本轮新增严格进程枚举、逐 harness
代码 allowlist、可收回 bounded worker、COM 文档访问前的 PID/creation/exe/HWND→PID 校验，
并按 Autodesk execution-context / read-only locking 文档修正插件 guard。离线复验为
`safe_process 43/43`、`bounded worker 6/6`、`repl probe 32/32`、targeted pyflakes 0。
但是这些不是 live PASS：项目所有者尚未确认独占授权环境，新的 GUI harness 尚未解决
launcher-stub/child handoff ownership，DAP 暂停态 managed LispFunction 的实际 execution
context 也尚未重新真机验证。

第三审计还撤回了 `T01-2-partial` 的安全 PASS 解释：旧 `run-acad-gui-test.sh` 在启动后
按安装目录重新扫描并选择 `acad.exe` PID，存在并发用户进程误归属竞态。脚本已永久退休；
旧结果仅保留为历史 GUI 行为观测，不能证明“只终止自己实例”。

### 4.2 环境

- `SECURELOAD` 负例无法自然复现（本机既有 profile 已为 0）→ 记 `BLOCKED`，不伪造 PASS。
- 检测到**并发第三方 AutoCAD 活动**（R24.2 profile 的 TRUSTEDPATHS 被追加 20 条
  `E:\360data\TEMP\hosttest-*`，非本项目）→ 影响独占性与 G01/G03 可复现性，需所有者确认。
- AutoCAD 2015/2019/2021 无本地安装 → `BLOCKED_NO_LOCAL_INSTALL`。
- 2016/2022 内核启动失败（宿主环境问题，未到达 NETLOAD）。
- 2023/2025 缺 `accoreconsole.exe` → 本方法不适用，**不等于不受支持**。

### 4.3 许可证

本机所有 AutoCAD 宿主 `license_provenance=unverified`。**本阶段结果一律标记
`LICENSE_UNVERIFIED`，在项目所有者确认前不得作为 Gate 放行证据。**

## 5. 审查中主动撤回的声明

| 原声明 | 现表述 |
|---|---|
| 「受保护表达式 `-1→1` 已用于陈旧性反证」 | **撤回**：该改动在运行后才写入脚本，从未运行 |
| 「`hover` 拒绝所有用户函数」 | 只测了**该函数**在该上下文 |
| 「`repl` 的 `result` 恒空」 | 本机观测，非普遍保证 |
| 「约 8 次终止且未造成损害」 | 撤回具体次数与「无害」表述；历史影响无法确证 |
| 「安全已修复」 | 撤回；harness 尚未通过审查，仅记录已采取的整改 |
| 「COM 超时 ⇒ 所有插件路径不可能」 | 限定为 COM 路径的关联观测；插件路径已证明可行 |

## 6. 交付物

| 类别 | 内容 |
|---|---|
| 证据 | `T01-1/`、`T01-2-gui/`、`T01-3/`、`T01-5/`、`tooling/` |
| 状态 | `status.json`（词表合规，`make-manifest.py` 校验通过）、`manifest.json` |
| 整改记录 | `SAFETY-INCIDENT-process-cleanup.md`、`REVIEW-REMEDIATION-round2.md`、`THIRD-AUDIT-LIVE-SAFETY.md` |
| 代码 | `src/Plugin.Shared/`（含 `LiveReadLispFunction.cs` 仪表化探针）、两 Shell 均 0 错误、无 Autodesk 程序集泄漏 |
| 工具 | `tools/safe_process.py`、`tools/selftest.sh`、DAP 客户端与各 T01-5 探针 |

## 7. 建议的下一步（需所有者裁决）

1. **先修复安全审查项**（COM 有界工作进程、上下文文档核实、空闲正对照），
   再恢复真机实验。
2. **明确 A15 的通道定义**：验收写的是「经 Plugin 合法读取」。本阶段证明的形态是
   「插件回调经 DAP `repl` 触发」——若这符合 A15 原意，应**显式确认**；
   若不符合，属**范围变更**，需所有者裁决，**不得由实施方自行降级**。
3. **确认独占实验窗口**（停止并发第三方 AutoCAD 活动）。
4. **确认许可证来源**，否则本阶段证据不能用于 Gate 放行。
5. 补 T01-4（LSP 结果/中文路径）与 T01-6（版本边界），并完成 T01-2b 目标选择。
