# CadBridge P0 报告 — 环境、来源与实施基线

**Run ID**：`20260917T083402Z` · 日期：2026-09-17 · 执行者：实施 AI（自主执行）
**覆盖需求**：R19、R21、R22、R23 · **任务**：T00-1 … T00-5
**结论**：**P0 未完成（INCOMPLETE）**。环境、来源与兼容矩阵盘点已完成并留证；但**依赖锁定（T00-2）仍未完成**，来源审计也只覆盖其实际抓取的条目。见 §6 / §7。

> 本报告经独立复核后修订。修订点：① 撤回“目录被外部进程重命名/环境抖动”的说法——首次盘点记录即为 `AutoCAD 2024.1.9`（空格），不存在改名事件；② T00-2 由 PASS 改为 BLOCKED；③ `running-cad-processes.json` 由空文件改为有效空数组。

> 本报告所有结论均可由 `docs/evidence/P0/20260917T083402Z/` 下的机器可读产物复算。
> 任何"未验证"的地方已显式标注，未以推断代替证据。

---

## 1. 执行摘要

| 项目 | 结果 |
|---|---|
| 现场 AutoCAD 独立安装 | **15** 个（另有 6 个嵌套/补丁目录命中，已排除） |
| 支持范围内（2015–2026）独立安装 | **12** 个（2016/2016.0.11/2017/2018×2/2020/2022/2023/2024×3/2025/2026） |
| 缺失年份 | **2015 / 2019 / 2021 → BLOCKED_NO_LOCAL_INSTALL** |
| .NET Framework | **4.8.09037**（release 533325）→ net48 插件前提满足 |
| .NET SDK | 10.0.400（另有 9.0.314）；运行时 8/9/10 齐全 |
| **Modern 分界（关键发现）** | **2025**：2025 与 2026 的 `acdbmgd.runtimeconfig.json` 为 `tfm: net8.0`；2024 及以前无该文件（.NET Framework） |
| MCP C# SDK | 最新稳定 **2.2.0**（Core / AspNetCore 同版本），NuGet 可达 |
| **调试端点（关键发现）** | 完整 DAP 词汇位于进程内 **`vl_u.crx`**（engine_score 9/9）；`AutoLispDebugAdapter.exe` 仅为会话宿主 |
| dbg adapter 文件存在 | 2022 / 2024（1.5、1.7、1.9）/ 2026；**2025 缺失** |
| 安全基线 | 现有 profile `SECURELOAD=0`、部分 `LISPSYS=2`（**均为既存状态，本项目未修改**） |

## 2. 三个改变实施策略的发现

### 2.1 Modern/Legacy 分界由 2025 起，而非按年份标签

`acdbmgd.runtimeconfig.json` 只在 2025 与 2026 出现，内容为 `"tfm": "net8.0"`（引用 `Microsoft.NETCore.App` 8.0.0 + `Microsoft.WindowsDesktop.App` 8.0.0）。2024 及以前宿主仅有 `acad.exe.config`（含 `bindingRedirect`），即 .NET Framework。

→ **ADR-06 的两目标方案得到现场证实**：Legacy = net48（≤2024），Modern = net8.0-windows（≥2025）。这也意味着 PRD §4.2 表格中"2025 = Modern"与现场一致，而**2024 必须用 net48**（不能按"较新即 net8"推断）。

→ **尚未观察到 2026 Update 1.2 的 .NET 10 变化**：现场 2026 的 runtimeconfig 仍是 net8.0。Update 级别未确认（见 §6 BLOCKED-4），A19 的"2026 ≤1.1 / ≥1.2 分列"尚未展开。

### 2.2 `AutoLispDebugAdapter.exe` 不是 DAP 引擎

`tools/probe-debug-adapter.py` 对文件做 DAP 词汇扫描（静态字符串，只证明"包含这些 token"）：

| 文件 | engine_score | 含有的 DAP 词汇 |
|---|---|---|
| `vl_u.crx`（2026，进程内 VLISP 模块） | **9 / 9** | setBreakpoints、stackTrace、scopes、variables、evaluate、continue、next、stepIn、stepOut、threads、stopped、output、breakpoint、variablesReference、frameId、threadId、configurationDone、sourceModified、runtimeerror、Content-Length |
| `AutoLispDebugAdapter.exe`（2022/2024/2026） | 0（仅握手+参数） | initialize、initialized、attach、launch、disconnect、terminate、processId、program、`Error: Missing program argument`、Content-Length |

`AutoCAD 2026` 全目录扫描 2367 个文件，只有 `vl_u.crx` 达到 9/9；其余高分命中全部是 Roslyn / Qt / Electron `bundle.js` 等无关二进制。

→ **对 P1 的直接价值**：避免把 DAP 客户端对着错误的进程/端点设计。三份 adapter 的 SHA-256 已记录，且 2022/2024/2026 的 adapter 互不相同（不是同一文件复制）。

→ **证据等级声明**：静态字符串**只**证明 token 存在，**不**证明线上行为、参数语义或顺序。A-H1/A-H2/A-H3/A-H4 必须由 T01-3 的真实 DAP 会话确认。

### 2.3 安全基线已使"SECURELOAD 阻挡"负例无法本地复现

现有用户 profile 中 `SECURELOAD` 已为 `0`（例：`R25.1\ACAD-9101:804\<<未命名配置>>` 为 `LISPSYS=2, SECURELOAD=0`）。

→ A01 要求"不降低 SECURELOAD"，本项目**不会**修改该值；但 A01/A18 中"策略阻挡加载 → `LOAD_BLOCKED_BY_POLICY`"这一负例**在当前现场无法自然复现**，记为 **BLOCKED**，不使用伪造手段补绿。

→ 同时记录到：历史 agent 会话曾在 2014 profile 下创建 `CodexAddPathFull_20260723_163200` 等 profile。这些**不是**本次创建，且 P1 不得以新建/修改 profile 作为加载条件。

## 3. 产物清单

| 产物 | 路径 | 状态 |
|---|---|---|
| 环境/依赖盘点（机器可读） | `docs/evidence/P0/<run>/raw/autocad-inventory.json` | PASS |
| 文档基线 hash | `docs/evidence/P0/<run>/raw/documents.json` | PASS |
| .NET SDK/运行时 | `docs/evidence/P0/<run>/raw/dotnet-info.txt` | PASS |
| .NET Framework | `docs/evidence/P0/<run>/raw/dotnet-framework.json` | PASS |
| OS | `docs/evidence/P0/<run>/raw/os.json` | PASS |
| ObjectARX 本地 SDK 引用程序集 | `docs/evidence/P0/<run>/raw/objectarx-assemblies.json` | PASS |
| DAP 端点定位（2026 全目录） | `docs/evidence/P0/<run>/raw/dap-localize-autocad-2026.json` | PASS |
| AutoCAD 安全基线（A01 before） | `docs/evidence/P0/<run>/raw/cad-security-baseline.json` | PASS |
| 来源/许可登记 | `docs/evidence/P0/<run>/raw/source-register.json` | 由独立审计补齐 |
| 兼容矩阵初稿 | `docs/design/compatibility-matrix.md` | PASS（初稿） |
| 构建基线 | `docs/design/build-baseline.json` | PASS（包版本待 pin） |
| P1 测试计划 | `docs/design/p1-test-plan.md` | PASS |
| 盘点工具 | `tools/inventory-autocad.ps1` 等 5 个 | PASS |

## 4. 依赖与选库决定（T00-2）

| 选型 | 决定 | 依据 | 验证状态 |
|---|---|---|---|
| Host TFM | **net10.0**（自包含 win-x64 计划） | Host 不受 CAD 进程约束；.NET 8 支持止于 2026-11-10（DESIGN S02）；本机 SDK 10.0.400 | 现场 SDK 已确认 |
| Plugin Legacy | **net48** | 2024 及以前宿主为 .NET Framework；本机 4.8.09037 | 现场已确认 |
| Plugin Modern | **net8.0-windows** | 2025/2026 宿主 runtimeconfig = net8.0 | 现场已确认 |
| MCP SDK | **ModelContextProtocol / .AspNetCore 2.2.0** | 官方 C# SDK；NuGet 已确认存在该版本 | 存在性确认；**包元数据/依赖/许可待 P0 审计** |
| Schema 验证 | 待定（候选 System.Text.Json + 生成式约束） | DESIGN D03 要求单一来源，禁止三套手写 | **PENDING** |
| 本地检索 | 待定（候选 Lucene.NET） | DESIGN D04 要求成熟本地实现，不自创排名 | **PENDING**：NuGet 上 `lucenenet` 主包仅 1.0.0/1.0.1，需确认现代包名与许可 |
| 日志 | Microsoft.Extensions.Logging（候选 Serilog 文件 sink） | DESIGN D14 要求滚动文件 + 许可审查 | **PENDING** |

**未采用/拒绝项**：`autocad.net`（第三方重打包 Autodesk 程序集，再分发许可需独立审查，不进入发布包）；生产 Python/Node 常驻进程（DESIGN D04/D15 明确不为小功能引入）。

## 5. 版本边界与调试能力结论（T00-5 / T01-6 前置）

| 年份 | 宿主运行时 | adapter 文件 | 插件 TFM | P0 调试结论 |
|---|---|---|---|---|
| 2016 / 2017 / 2018 / 2020 | .NET Framework | 无 | net48 | **EXPLORATORY_PENDING**（文件缺失 ≠ 官方不支持） |
| 2022 / 2024 | .NET Framework | 有 | net48 | **UNVERIFIED**（文件存在 ≠ DAP 可用） |
| 2025 | **.NET 8** | **无** | net8.0-windows | **UNVERIFIED**：与 2026 同为 Modern 却缺 adapter，须查明分发方式 |
| 2026 | **.NET 8** | 有 | net8.0-windows | **UNVERIFIED**；UPDATE 级别待确认 |
| 2015 / 2019 / 2021 | — | — | — | **BLOCKED_NO_LOCAL_INSTALL** |

## 6. BLOCKED / NOT_RUN 清单（不掩盖）

| ID | 项 | 状态 | 原因 |
|---|---|---|---|
| BLOCKED-1 | 2015 / 2019 / 2021 真机验证 | **BLOCKED_NO_LOCAL_INSTALL** | 本机未安装；不由执行 AI 自行安装 |
| BLOCKED-2 | `SECURELOAD` 策略阻挡加载负例（A01/A18） | **BLOCKED** | 现场 `SECURELOAD` 已为 0；本项目不修改设置以制造负例 |
| BLOCKED-3 | 2025 官方调试能力判定 | **BLOCKED_PENDING_INVESTIGATION** | adapter 文件缺失；需确认官方分发/安装前提 |
| BLOCKED-4 | 2026 Update 1.1 / 1.2 分列 | **BLOCKED_PENDING_UPDATE_LEVEL** | 未取得 UPDATE 级版本号；runtimeconfig 仍为 net8.0，未观察到 .NET 10 |
| BLOCKED-5 | 宿主许可可用于验收证据 | **BLOCKED_PENDING_OWNER** | 所有宿主 `license_provenance=unverified`；一个安装树存在第三方补丁材料 |
| NOT_RUN | 全部真机运行/加载/调试/性能/Agent 测试 | NOT_RUN | 属 P1 及以后 |

## 7. 待项目所有者裁决（4 项）

| ID | 问题 | 为何需要人决 |
|---|---|---|
| Q-1 | 哪些宿主许可可用于 Gate 验收证据？ | 涉及许可与合规，执行 AI 无法自证 |
| Q-2 | 缺失年份 2015/2019/2021 如何处置（安装 / 缩小宣称范围 / 外部执行） | 改变 A19 宣称范围 |
| Q-3 | 若 T01-5 只得到 `DEBUG_QUERY_UNSAFE`，是否接受受限方案 | 需批准的范围降级（DESIGN ADR-08） |
| Q-4 | 是否允许为测试宿主建立专用 profile | 影响加载可复现性与现场整洁 |

## 8. 结论与下一步

**P0 退出条件核对**（IMPLEMENTATION_PLAN §3）：
- ❌ **未满足**：并非所有选型都已锁定。Schema 验证库、本地检索库、日志库仍为未定；MCP SDK 仅确认版本存在，包元数据/依赖/许可只部分核验。故 P0 不能判 PASS。
- ⚠️ source 与 binary 许可分开记录（binary 侧标 `license_provenance=unverified`），但来源审计只覆盖其抓取的 Sxx 条目，未覆盖 DESIGN D17 全部来源。
- ✅ M7（Backend 预留）无额外 Runtime 依赖：P0 未引入 Python/Node/APS/CoreConsole 生产依赖。
- ✅ P1 具备**至少一个 Legacy（2024）+ 一个 Modern（2026）**真机环境。
- ✅ 缺失宿主（2015/2019/2021）已清楚标 BLOCKED，未伪造运行记录。
- ⚠️ 许可可用性（Q-1）未决。

**下一步**：先完成 P0 剩余锁定（Schema/检索/日志库、逐项许可）并修正证据校验工具，再进入 P1 的完整 AutoCAD 会话项（T01-2 … T01-5）。

## 9. 修订记录

| 日期 | 修订 | 原因 |
|---|---|---|
| 2026-09-17 | 初版 | P0 盘点 |
| 2026-09-17 | 独立复核后修订：撤回“目录被外部进程重命名/环境抖动”叙述；T00-2 由 PASS 改 BLOCKED；`running-cad-processes.json` 修正为有效空数组 | 复核发现原叙述缺乏证据：首次盘点记录即为 `AutoCAD 2024.1.9`（空格），无改名事件 |
