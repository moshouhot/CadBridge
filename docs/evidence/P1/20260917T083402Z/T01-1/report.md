# P1 / T01-1 — 最小插件与调度：真实加载证据

**Run ID**：`20260917T083402Z` · 日期：2026-09-17 · 状态：**PASS（仅限下列已执行子项）**
**覆盖需求**：R19（前置）· **Gate 关联**：G01 **未通过**（见 §7）

> 本报告经独立复核后**已修订**。上一版包含若干过度归因，修订点集中在 §1.2。
> 只陈述实际执行并留有可复算产物的结论；未执行项一律 NOT_RUN。

---

## 1. 结论

### 1.1 已证明（有真实 AutoCAD 进程证据）

| 断言 | 结果 | 证据 |
|---|---|---|
| 同一份共享源码编译出两个 Shell（net48 / net8.0-windows） | PASS | `build/*-build.log` |
| 两 Shell 均能在**真实 AutoCAD 内核进程**中 NETLOAD 并被调用 | PASS | `matrix/*.txt` |
| 报告的运行时是**实测**而非编译常量 | PASS | §1.3 |
| 合法主线程 + DocumentLock + Transaction 成立 | PASS | `lock_and_transaction: OK` |
| 受控写入真实提交，并可由**独立进程**重新打开 DWG 后读回几何 | PASS | §1.4 |
| 非法半径被拒且**无半成品实体** | PASS | §1.4 |
| 产物不含任何 Autodesk 程序集 | PASS | §1.5 |
| 两 Shell 的分工是经验必需 | PASS | §1.6 |

### 1.2 上一版的错误（已撤回）

| 原说法 | 更正 |
|---|---|
| "`AutoCAD_2024.1.9` 被外部进程重命名，环境抖动" | **撤回**。首次盘点记录的就是 `AutoCAD 2024.1.9`（空格）；不存在改名或抖动的证据。`Directory.Build.props` 中的相关叙述已删除。 |
| "`acmgd.dll` 导致加载失败" | **降级为未证实的假设**。当时两个产品程序集因一次错误编辑变成**空程序集**（`Compile Include` 被误删），无法据此归因。改用 Core 门面后确能加载，但**没有对照实验**证明 acmgd 是唯一原因。 |
| "net48 外壳走 .NET Framework 兼容模式，故与 net8 不等价" | **撤回**。原 `FrameworkDescription` 在 Legacy 分支返回的是**编译常量**。已改为分别报告 `compiled_target` 与实测 `runtime_framework`；实测显示在 2024 上 net48 外壳运行于真实 .NET Framework CLR。 |
| "再次统计实体数即为独立 Oracle" | **加强**。同进程自读不构成独立 Oracle；已改为**保存 DWG → 新进程重新打开 → 读回几何**。 |
| 示例圆半径 | 澄清：`CBBRIDGEPINGCIRCLE` 使用 **r=10**（对应 F-CAD 的 C1），非 3。 |

### 1.3 实测运行时（三个互不可替代的量）

```text
# Legacy 外壳 (net48) 在 AutoCAD 2024.1.7 内核中
compiled_target=net48; clr=4.0.30319.42000; runtime_framework=.NET Framework 4.8.9261.0;
framework_clr=True; arch=x64; asmver=0.1.0.0

# Modern 外壳 (net8.0-windows) 在 AutoCAD 2026 内核中
compiled_target=net8.0-windows; clr=8.0.22; runtime_framework=.NET 8.0.22;
framework_clr=False; arch=x64; asmver=0.1.0.0
```

`compiled_target` 是构建常量，**明确标注**；`clr` 与 `runtime_framework` 由运行时自报。
二者在 Legacy 宿主上一致（都是 .NET Framework），在 2026 上 Legacy 外壳报告
`clr=8.0.22` + `framework_clr=True`，说明 net48 程序集运行在 .NET 8 的兼容模式上——
但**这是观测，不是"等价性"结论**。

### 1.4 独立验证（写 → 保存 → 新进程重开 → 读回）

方法：进程 A 建圆并 `SaveAs` 到一次性 DWG；进程 B **重新启动**、打开该 DWG、读回几何。
两个进程无共享内存状态，因此不是"自己证明自己"。

```text
# 进程 A（创建 + 保存）
model_space_entities: 0
CBBRIDGECIRCLE OK handle=2B8 layer=0 center=100,100,0 r=10
CBBRIDGESAVEAS OK path=F:/CadBridge-run/20260917T083402Z/fixture.dwg
model_space_entities: 1

# 进程 B（独立重开同一文件）
drawing: F:\CadBridge-run\20260917T083402Z\fixture.dwg
CIRCLE handle=2B8 layer=0 center=100,100,0 r=10 normal=0,0,1
circle_count: 1
layers: 0
```

**负例（无部分效果）**

| 输入 | 结果 | 实体数 |
|---|---|---|
| radius = 0 | `CBBRIDGECIRCLE REJECTED invalid radius=0` | 1（未增加） |
| radius = −5 | `CBBRIDGECIRCLE REJECTED invalid radius=-5` | 1（未增加） |
| radius = 50（正对照） | `CBBRIDGECIRCLE OK handle=2C9 ... r=50` | 2 |

DWG 产物：`F:\CadBridge-run\20260917T083402Z\fixture.dwg`，32,785 字节。
该文件位于**非 Nextcloud 同步目录**，不入库（见 §8）。

### 1.5 不含 Autodesk 程序集

构建目标已加入强制检查：若 `ReferenceCopyLocalPaths` 中出现
`acdbmgd/accoremgd/acmgd`，构建立即 **Error**（见 `Directory.Build.props` 的
`CadBridgeRecordRefs`）。当前两 Shell 输出目录仅含自身程序集。

### 1.6 兼容矩阵（真实执行，A19 核心证据）

方法：每个 Shell × 每个带 `accoreconsole.exe` 的宿主，执行
`CBBRIDGEINFO → CBBRIDGEPINGCIRCLE → CBBRIDGEINFO`。

| 宿主 | 内核版本 | net48 外壳 | net8 外壳 |
|---|---|---|---|
| 2016 (`R20.1.107.0.0`) | — | BLOCKED_HOST_ENV | BLOCKED_HOST_ENV |
| 2017 (`R21.0.104.0.0`) | U.107.0.0 | ✅ 加载+建圆+回读 | ❌ 命令未注册 |
| 2018 (`R22.0.161.0.0`) | — | ✅ | ❌ |
| 2020 (`R23.1.172.0.0`) | — | ✅ | ❌ |
| 2022 (`R24.1.191.0.0`) | S.191.0.0 | BLOCKED_HOST_ENV | BLOCKED_HOST_ENV |
| 2023 (`R24.2.191.0.0`) | — | NOT_RUN（无 accoreconsole） | NOT_RUN |
| 2024.1.5 (`R24.3.191.0.0`) | — | ✅ | ❌ |
| 2024.1.7 (`R24.3.212.0.0`) | U.212.0.0 | ✅ | ❌ |
| 2025 (`R25.0.154.0.0`) | — | NOT_RUN（无 accoreconsole） | NOT_RUN |
| 2026 (`R25.1.74.0.0`) | W.74.0.0 | ✅（CLR 8.0.22） | ✅（CLR 8.0.22） |

**三条结论**

1. **net8 外壳在 ≤2024 宿主上不加载**（命令未注册）→ **两 Shell 均为必需**，ADR-06 得证。
2. **net48 外壳在 2026（.NET 8 宿主）上仍可加载执行** → 但这**不**构成"2026 只需 net48"的理由；
   Modern 宿主的能力差异与 2026 Update 1.2 的 .NET 10 演进仍未验证。
3. **2023 / 2025 缺 `accoreconsole.exe` ≠ 该年份不可测**。它只表示本方法不适用，
   必须由完整 `acad.exe` 会话覆盖，记为 NOT_RUN 而非 FAIL、也不记为不支持。

## 2. 失败项归类（不误报为不兼容）

| 宿主 | 现象 | 归类 | 依据 |
|---|---|---|---|
| 2016 | `无效的配置路径/文件夹: C:\Users\...\AppData\Local\Autodesk\AutoCAD 2016\R20.1\chs\`，随后 `Error handler re-entered. Exiting now.` | **BLOCKED_HOST_ENV** | 启动阶段即失败，未到达 NETLOAD |
| 2022 | `ERROR: Something went wrong. ErrorStatus=53.`，`LogFilePath has been restored to ''` | **BLOCKED_HOST_ENV** | 启动阶段失败，未到达 NETLOAD |

两者均为宿主安装/配置问题，需环境处置，非插件缺陷。

## 3. 方法边界（不得越界宣称）

| 本方法**能**证明 | 本方法**不能**证明 |
|---|---|
| 真实 AutoCAD 内核进程中的程序集加载与命令注册 | 编辑器 UI 命令、交互、拾取 |
| 合法主线程 + DocumentLock/Transaction | `view.capture` / zoom / 遮挡行为 |
| 实测运行时与宿主产品版本 | DAP 调试（adapter 不在内核控制台内） |
| 写入的真实提交与**跨进程**独立回读 | Pipe、MCP/REST/CLI、事件、Token 预算 |
| 两 Shell 的加载边界 | **完整 `acad.exe` GUI 会话下的同一结论** |

`accoreconsole` 与 GUI 共用 acdbmgd/accoremgd 内核，但**不含 acmgd**。
因此 **G01 仍需完整 AutoCAD 会话**，本报告不替代它。

## 4. 产物清单

```text
T01-1/
  build/legacy-build.log, modern-build.log      共享源 → 两 Shell 构建
  accoreconsole/*.txt|.raw|.scr                 单点验证（含早期失败对照）
  matrix/matrix-summary.tsv                     完整矩阵结论
  matrix/<host>-<shell>.{scr,raw,txt}           每组合原始证据
  independent/step1-create-and-save.*           进程 A：建圆 + 保存
  independent/step2-reopen-and-verify.*         进程 B：独立重开 + 读回
  independent/step3-reject-radius-0.*           负例：r=0 拒绝
  independent/step4-reject-radius-negative.*    负例：r=-5 拒绝
  independent/step5-accept-radius-50.*          正对照：r=50 接受
```

## 5. 工具链缺陷与修复（已加入自检）

本轮暴露的工具问题，均已在脚本中修复，避免"CAD 退出码 0 即 PASS"：

| 缺陷 | 影响 | 修复 |
|---|---|---|
| MSYS 把 `/s` 改写成 `S:/` | accoreconsole 只打印 Usage，脚本未执行 | `MSYS_NO_PATHCONV=1` + `MSYS2_ARG_CONV_EXCL='*'` |
| Windows python 无法打开 `/f/...` | 路径转换静默返回空 | 路径转换改为纯 bash/sed |
| `$(dirname "$0")` 为相对路径 | 解码器找不到，`.txt` 未生成 | 用绝对 `TOOLS_ABS` |
| `printf '%s'` 无换行 | 插件路径与下一命令拼接 | 改为 `printf '%s\n'` |
| 解码失败被静默忽略 | 空 `.txt` 被当成"命令未注册" | 解码非零退出时告警；矩阵记录 `note` 区分原因 |
| 相对 `--out-prefix` | accoreconsole 的 CWD 不同导致找不到脚本 | 一律转绝对路径 |

## 6. 未决（P1 其余项）

| 项 | 状态 |
|---|---|
| T01-2 绿色 Bootstrap 与准确目标（PID/启动时间/HWND 核验、重复 attach、ATTACH_AMBIGUOUS） | **NOT_RUN** |
| T01-3 官方 DAP 独立 Client | **NOT_RUN** |
| T01-4 LSP 结果/中文/交互 | **NOT_RUN** |
| T01-5 暂停点图面与防死锁（核心不可降级项） | **NOT_RUN** |
| T01-6 版本边界与 2015–2020 调试探索 | **NOT_RUN**（P0 仅完成静态引擎定位） |
| Pipe 后台收发与正式调度 | **NOT_RUN** |

## 7. Gate 状态

| Gate | 状态 | 说明 |
|---|---|---|
| **G01** | **NOT_RUN** | 需完整 `acad.exe` 会话：GUI 内 NETLOAD、精确 PID 附加、两 Shell、Pipe 最小读写 |
| **G02** | **NOT_RUN** | 需真实 DAP 会话与暂停点查询 |

本报告**不**支持宣布 G01 通过。

## 8. 证据隔离

- 一次性 DWG 与运行目录位于 `F:\CadBridge-run\`（**非** Nextcloud 同步目录）。
- 证据目录仅保存脚本、原始输出与解码文本，**不含**用户 DWG、凭据或 DPAPI 材料。
- 矩阵运行使用新建空图（`Drawing1.dwg`）与一次性 fixture，未触碰任何用户图纸。
