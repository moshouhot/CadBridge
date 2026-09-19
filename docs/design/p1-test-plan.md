# CadBridge P1 测试计划 — 最小真实链路与高风险 PoC

版本：1.0 · 日期：2026-09-17 · 对应阶段：IMPLEMENTATION_PLAN §4 (P1 / T01-1…T01-6) · 上游：P0 证据 `docs/evidence/P0/20260917T083402Z/`

> 本文件是**测试方案**，不是执行结果。P1 的任何子项在执行前状态均为 NOT_RUN。
> Fakе/编译成功/Adapter 文件存在均**不能**替代真机通过（ACCEPTANCE §1）。

## 0. P1 目标与停点

P1 只验证高风险技术前提，不做产品级实现：

| 编号 | 必须证明的前提 | 失败后果 |
|---|---|---|
| T01-1 | 两 Shell（net48/net8）可编译并在真机合法主线程上下文执行 CAD API | G01 失败 → 停依赖该运行环境的实现 |
| T01-2 | 可由明确 PID/启动时间/路径附加并临时 NETLOAD，不误伤其他实例 | G01 失败 |
| T01-3 | 无 VS Code 的真实 DAP attach/断点/单步/栈/变量 | G02 失败 → 停正式 Debugger |
| T01-4 | Lisp load/eval/call/run_command 返回真实值、区分提交/完成、中文路径 | G02 失败 |
| T01-5 | 调试暂停点仍能合法读取该实体且不死锁 | G02 失败 → **核心目标不可暗降级** |
| T01-6 | 版本边界：2021–2026 强制；2015–2020 探索结论 | 旧版探索不阻塞 V1 |

**停点规则**：G01/G02 形成的证据包与结论须停下汇报。G02 未过不得开展依赖真调试的 P5；不得以"先做 P3/P4 再回头补调试"绕过。

## 1. 环境前提与目标选择

### 1.1 选定测试目标（依据 P0 盘点）

| 角色 | 宿主 | 版本 | 插件 TFM | 理由 |
|---|---|---|---|---|
| **Legacy 主目标** | `D:\Program Files\Autodesk\AutoCAD_2024.1.9\AutoCAD 2024` | R24.3.236.0.0 | net48 | 2024 属 Legacy（`.NET Framework`），adapter 文件存在，为 2021–2026 强制调试范围内的最高 Legacy 补丁 |
| **Modern 主目标** | `D:\Program Files\Autodesk\AutoCAD 2026` | R25.1.74.0.0 | net8.0-windows | 唯一的 Modern 宿主，`acdbmgd.runtimeconfig.json` = net8.0，adapter 存在 |
| Legacy 备目标 | `...\AutoCAD 2022.1.5\AutoCAD 2022` | R24.1.191.0.0 | net48 | 官方调试范围下界（2021+）附近，adapter 存在 |
| 边界目标 | `...\AutoCAD_2025.1.1\AutoCAD 2025` | R25.0.154.0.0 | net8.0-windows | Modern 宿主但 **无** adapter 文件 → 验证"文件存在≠调试可用"的双向命题 |

### 1.2 硬前提（任一不满足即 BLOCKED，不自行绕过）

| ID | 前提 | 现场状态（P0） | 处置 |
|---|---|---|---|
| PRE-1 | 目标宿主许可可由本项目用于验收证据 | **未确认** | **须项目所有者确认**；未确认前所有证据标 `LICENSE_UNVERIFIED`，不得进入 Gate 放行 |
| PRE-2 | 至少一个 Legacy + 一个 Modern 真机 | 已具备（2024 / 2026） | OK |
| PRE-3 | 允许对测试宿主临时 NETLOAD | **未确认**（现有 profile `SECURELOAD=0`，但策略面仍需确认） | 见 §5 安全红线 |
| PRE-4 | 该版本 CAD 的 managed 引用程序集 | 2024：宿主目录 `ac*.dll`；2026：宿主目录；另有 ObjectARX-2021/2025 本地 SDK | OK |
| PRE-5 | 缺失年份 2015/2019/2021 | **BLOCKED_NO_LOCAL_INSTALL** | T01-6 记录为 BLOCKED，不冒充 |

### 1.3 不得触碰的现场

- 任何**用户正在使用的** CAD 会话（P0 观察到 PID 3836 已由用户关闭；不复现、不启动）。
- 用户 DWG。全部 PoC 只在**新建的一次性 fixture** 上进行（ACCEPTANCE §2 F-CAD 的 PoC 子集）。
- 现有 AutoCAD profile（含历史 `Codex*` profile）：P1 **不得**新建/修改 profile 作为加载条件。

## 2. P0 交付的关键技术假设（P1 必须验证或推翻）

| 假设 | P0 证据 | P1 验证点 |
|---|---|---|
| **A-H1**：`AutoLispDebugAdapter.exe` 是**会话宿主**，真正 DAP 词汇在进程内 `vl_u.crx` | `dap-localize-autocad-2026.json`：`vl_u.crx` engine_score=9/9，含 setBreakpoints/stackTrace/scopes/variables/evaluate/stepIn/stepOut/runtimeerror；`AutoLispDebugAdapter.exe` 仅含 initialize/attach/launch/processId/program/Content-Length | T01-3：以 adapter 为 DAP 端点能否完成完整调试会话？若不能，需确定正确端点 |
| **A-H2**：DAP 走 `Content-Length` 帧 | `Content-Length` 出现在 adapter 与 `vl_u.crx` | T01-3 抓取真实字节确认，不使用 Named Pipe 4 字节帧 |
| **A-H3**：attach 需要 `processId` + `program`（源码路径） | adapter 字符串 `Error: Missing program argument`、`processId` | T01-3 实测两种参数组合 |
| **A-H4**：厂商事件名 `runtimeerror` | `vl_u.crx` 含 `runtimeerror` | T01-3/F-ERROR 触发 Break on Error |
| **A-H5**：Modern 分界在 2025（net8 起点） | 2025/2026 有 `acdbmgd.runtimeconfig.json` tfm=net8.0；2024 及以前为 .NET Framework | T01-1 两 csproj 分别在对应宿主加载成功 |
| **A-H6**：2025 无 adapter 文件 | 盘点 `has_autolisp_adapter=False` | T01-6 判定 2025 官方调试能力（可能为宿主配置差异，非"不支持"） |

## 3. 测试项与通过准则

### T01-1 最小插件与调度（Legacy/Modern 双 Shell）

**方法**
1. 建 `src/Plugin.Shared`（共享源）+ `src/CadBridge.Plugin.Legacy`（net48）+ `src/CadBridge.Plugin.Modern`（net8.0-windows），`Reference Include` 用 `HintPath` 指向本机 SDK/宿主目录，**不复制** 厂商 DLL。
2. 实现最小命令：pipe 收发后台线程、`document.info`、`query.entities`（最小）、受控创建一个圆。
3. 记录 `Environment.Version`、`RuntimeInformation.FrameworkDescription`、`Assembly.GetExecutingAssembly()` 路径与 CAD `ProductVersion`，**不只打印自定义版本常量**。

**通过准则**
- 两 TFM 分别编译成功；`net48` 产物在 2024 宿主、`net8` 产物在 2026 宿主均可 NETLOAD 并被调用。
- 输出证据证明运行在**合法 CAD 主线程上下文**（如 `Application.DocumentManager.MdiActiveDocument` 可达，且命令由 `CommandMethod` 进入）。
- `DocumentLock` / `Transaction` 边界成立，未跨 await 持有锁。
- 包内**不含** `acmgd.dll`/`acdbmgd.dll`/`accoremgd.dll`。

**证据**：`docs/evidence/P1/<run>/T01-1/` — csproj、构建 hash、两次 NETLOAD 日志、Runtime 自报、包内 DLL 清单、命令返回。

### T01-2 绿色 Bootstrap 与准确目标

**方法**：按 A02 步骤。同版本两实例（2024.1.9 与 2024.1.7 同一 product version R24.3 但不同 build、或同 build 双开）、不同版本、权限不符、无效 PID、未运行时显式 launch、重复 attach。

**通过准则**
- `discover` 记录 PID / start_time / exe path；`attach --pid B` 仅 B 收到 NETLOAD，A/C 图纸与窗口状态不变。
- 无法证明 COM 对象 PID/HWND 归属时返回 `ATTACH_AMBIGUOUS`，**不**新建隐藏进程、**不** Dispatch 偷开实例。
- 重复 attach 复用同版插件；已加载旧版返回 `RESTART_REQUIRED`。
- "显式 launch 未运行版本"与"未运行时**不**自动 launch"两种行为都可复现。

**证据**：进程前后清单、HWND→PID 证明、插件身份读回、错误码响应。

### T01-3 官方 DAP 独立 Client（**最高风险**）

**方法**
1. 先做**零成本双向探查**：按 A-H1 分别以 `AutoLispDebugAdapter.exe` 为端点、以及观察 `vl_u.crx` 是否要求经由 adapter 建连，确定正确连接拓扑。
2. 用成熟 DAP 库或自写 `Content-Length` 客户端（非 VS Code UI）完成：`initialize` → `attach`（含 `processId`、`program`）→ 断点（F-LSP 第 6/8 行）→ 异常策略 → 运行 → `stopped` → `stackTrace` → `scopes` → `variables` → `stepIn/stepOver/stepOut` → `continue`。
3. 记录完整 DAP 收发顺序与关键事件，凭据脱敏。
4. 检验 adapter 返回的 `verified` 断点实际位置、错误扩展、源码 hash 绑定。

**通过准则**（ACCEPTANCE A14 PoC 子集）
- 无 VS Code UI 参与；真实 `stopped` 事件来自真实断点命中（非日志/重复执行/模拟栈）。
- 第 6 行进入 `cb-add`；执行第 2 行后 `x=2, y=3`；`stepOut` 回调用者 `p=3`。
- F-ERROR 开启 Break on Error 后在真实错误处停止，读到 `x="bad"`。
- 断点绑定到相邻可执行位置时返回 `verified` 实际位置并证明语义等价。
- 源码变更后旧断点/源码 hash 失效或要求重载。

**失败判定**：若 adapter 不能承担完整会话且无替代受支持路径 → 记录 `unavailable` + 证据；**2021–2026 因此 G02 失败**（不允许用日志/模拟补齐）。若仅"尚未找到正确拓扑"则继续探索，不提前下结论。

**证据**：原始脱敏 DAP transcript、真实 stopped/stack/scopes/variables、源码 hash、PID 证明。

### T01-4 LSP 结果、中文与交互

**方法**：load F-LSP；`eval (+ 1 2)`；`call cb-add(2)`；`run_command C:CBDBG`；中文/空格路径；MBCS/Unicode 组合；不可无损编码字符拒绝；reload 残留；getpoint/DCL 等待；客户端 timeout。

**通过准则**（A13 子集）
- 返回数值 3、实际 r=3 圆、`CBDBG_DONE` 输出并注明来源与 `output_coverage`。
- nil/T/list/string 保持类型（tagged value），不硬转字符串。
- 提交回执与完成回执**分开**；客户端 HTTP 断开**不**等于 Lisp 已停。
- reload **不**清旧定义/全局状态，并有明确说明。
- 无法表示的字符返回 `ENCODING_UNSUPPORTED`，不静默 `?` 替换。
- 检查 `LISPSYS`/日志设置前后值未变（注意：现场 2022 与 2026 profile 已 `LISPSYS=2`，需记录"本就如此"而非本项目改动）。

**证据**：源码字节/hash、编码、值、job 状态、实际图元、输出来源、设置前后值。

### T01-5 暂停点图面与防死锁（**核心不可降级项**）

**方法**：F-LSP 在第 8 行执行前暂停（第 7 行圆已创建）。保持**同一 stop_id**，经 Plugin 合法读取该圆中心与 r=3；确认尚未输出 `CBDBG_DONE`；随后 step/continue 仍可完成。并发尝试：普通 DB 写、另一客户端 continue、过期 stop_id 查询。

**通过准则**（A15 子集）
- 合法 live 查询真实且**无死锁/重入**，读回值与 DB 一致。
- 普通 DB 写返回 `DEBUG_BUSY`；非授权控制被拒；过期 stop_id 返回 `STALE_DEBUG_HANDLE`。
- **禁止**用"先 continue 再 query"通过；**禁止**以自动恢复程序再暂停模拟同一停止点。
- 若只能取得旧快照 → 必须带 staleness 且本项 **BLOCKED**，不得 PASS。
- 若数据库中间状态不安全 → 返回 `DEBUG_QUERY_UNSAFE`（安全退化，**不等于产品目标兑现**）；此种情况须提交范围决定给项目所有者，**不得自行降级**。

**证据**：相同 stop_id 时间线、DAP 与 Pipe 收发、DB 读回、continue 后结果、控制租约负例。

### T01-6 版本边界与 2015–2020 调试探索

**方法**
1. 2022/2024/2026 按 A-H1 定位调试端点并记录结论。
2. **2025**：adapter 文件缺失 → 判定是"官方未分发"、"需另行安装"还是"已内置于 `vl_u.crx`"（2025 是否也有 `vl_u.crx`？P1 核对）。
3. 2016/2017/2018/2020（本机可测的 Legacy）：对每一候选路线给证据/许可/依赖/局限；记录 `available` / `unavailable` / `unverified`。
4. 2015/2019/2021：**BLOCKED_NO_LOCAL_INSTALL**，记录，不冒充。

**通过准则**
- 每条结论附可复现命令与来源。
- **不得**用邻近年份代替缺失年份。
- 2015–2020 未找到受支持 Provider → 记录 `unavailable-with-evidence`，**不阻塞**其 Query/Edit/Batch/Lisp Runtime。
- 明确声明：官方范围 2021+；2015–2020 调试仅为探索。

## 4. Gate 出口判据（P1 结束时的汇报清单）

**G01**：A01–A03/A19 的 PoC 子集通过 — 准确附加 + 两 Shell + Pipe 最小读写有真机证据。
**G02**：A13 的 Lisp Runtime 子集按目标版本验证；A14–A15 对 **2021–2026** PoC 子集必须通过（特别是无 VS Code 真实调试与暂停查询）。2015–2020 Debugger 只要求完成探索、证据与 capability 结论。

汇报须逐项列出：修改与合同版本；已执行测试及证据路径；NOT_RUN / BLOCKED / FAIL 项；对应 Gate；影响范围；下一位 AI 可开始的任务。**不得**写"所有 Gate 都通过"却不列清单。

## 5. 安全红线（P1 全程适用）

| 禁止 | 理由 |
|---|---|
| 设置 `SECURELOAD=0`、持久新增 TRUSTEDPATHS、改 `LISPSYS` | DESIGN D13；ACCEPTANCE A01/A18 |
| 提权、写 HKLM Autodesk 键、改管理员策略 | D13 |
| 杀死/重启用户 CAD 进程 | PRD R02/R17 |
| 对用户 DWG 做任何写入 | ACCEPTANCE §1 |
| 自动保存/关闭文档 | D09 |
| 把 adapter 或 Autodesk 二进制复制进包 | DESIGN D15/D17 |
| 在已加载旧 DLL 的进程强盖 DLL | D13 |
| 自行安装缺失年份的 CAD | P0 退出规则 |

**受限环境的记录方式**：若加载被策略阻挡，记录 `LOAD_BLOCKED_BY_POLICY` + 策略来源 + 安全操作说明；**另需**在授权环境取得成功链路证据，二者不可互相顶替（A01）。

**证据隔离**：项目位于 Nextcloud 同步目录。`runtime/secrets/`、认证材料、含用户数据的证据**不得**写入同步目录；P1 的计划输出根为 `F:\CadBridge-run\<run_id>\`（非同步），仅将脱敏结果复制回 `docs/evidence/P1/`。

## 6. P1 目录与产物规划

```text
src/
  CadBridge.Contracts/          合同 DTO（P2 完善；P1 仅最小）
  Plugin.Shared/                共享源
  CadBridge.Plugin.Legacy/      net48
  CadBridge.Plugin.Modern/      net8.0-windows
  CadBridge.DapClient/          独立 DAP 客户端（P1 研究性）
tools/
docs/evidence/P1/<run_id>/
  manifest.json
  T01-1/ … T01-6/
```

## 7. 未决问题（需项目所有者裁决，不阻塞 P1 开始）

| ID | 问题 | 影响 |
|---|---|---|
| Q-1 | 哪些宿主的许可可用于验收证据？（PRE-1） | 证据有效性；未决期间全部标 `LICENSE_UNVERIFIED` |
| Q-2 | 2015/2019/2021 缺失年份如何处置（安装/缩范围/外部证据） | A19 完整宣称 |
| Q-3 | 若 T01-5 只能得到 `DEBUG_QUERY_UNSAFE`，是否接受受限产品方案 | 需批准的范围变更 |
| Q-4 | 是否允许在测试宿主创建专用 profile 以规避现有配置干扰 | 影响 T01-2/T01-4 可复现性 |
