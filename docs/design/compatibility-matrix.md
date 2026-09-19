# CadBridge 兼容矩阵 — P0 初稿（现场盘点，非验证结论）

版本：0.1（P0 初稿）· 日期：2026-09-17 · 上游基线：DESIGN.md §4.2 / D15 · 证据：`docs/evidence/P0/20260917T083402Z/`

> **本表是现场环境**盘点**结果，不是能力验证结论。**
> `adapter_present` 只表示文件存在，**不表示 DAP 可用**；`core_host` 只表示宿主为 .NET (Core) 运行时。
> 所有运行/调试能力列在 P1–P7 执行前保持 NOT_RUN。

## 1. 现场已安装宿主（15 个独立安装，21 个 acad.exe 命中）

| # | 产品版本 | 独立安装目录 | 宿主运行时 | adapter 文件 | 本地 ObjectARX SDK | 计划插件 TFM |
|---|---|---|---|---|---|---|
| 1 | R18.2.205.0.0 (2012) | `D:\Program Files\Autodesk\AutoCAD 2012 - Simplified Chinese` | .NET Framework | 无 | — | **范围外**（非 2015+） |
| 2 | R19.1.108.0.0 (2014) | `D:\Program Files\Autodesk\AutoCAD 2014` | .NET Framework | 无 | ObjectARX-2014 | **范围外**（非 2015+） |
| 3 | R20.1.107.0.0 (2016) | `D:\Program Files\Autodesk\AutoCAD 2016` | .NET Framework | 无 | — | net48 |
| 4 | R20.1.107.0.27 (2016.0.11) | `D:\Program Files\Autodesk\AutoCAD 2016.0.11\AutoCAD 2016` | .NET Framework | 无 | — | net48 |
| 5 | R21.0.104.0.0 (2017) | `D:\Program Files\Autodesk\AutoCAD 2017` | .NET Framework | 无 | ObjectARX-2017 | net48 |
| 6 | R22.0.161.0.0 (2018) | `D:\Program Files\Autodesk\AutoCAD 2018` | .NET Framework | 无 | ObjectARX-2018 | net48 |
| 7 | R22.0.161.0.0 (2018) | `D:\Program Files\Autodesk\AutoCAD_2018.1.2\AutoCAD 2018` | .NET Framework | 无 | — | net48 |
| 8 | R23.1.172.0.0 (2020) | `D:\Program Files\Autodesk\AutoCAD 2020` | .NET Framework | 无 | — | net48 |
| 9 | R24.1.191.0.0 (2022) | `D:\Program Files\Autodesk\AutoCAD 2022.1.5\AutoCAD 2022` | .NET Framework | **有** | ObjectARX-2021 | net48 |
| 10 | R24.2.191.0.0 (2023) | `D:\Program Files\Autodesk\AutoCAD_2023.1.5\AutoCAD 2023` | .NET Framework | 无 | — | net48 |
| 11 | R24.3.191.0.0 (2024.1.5) | `D:\Program Files\Autodesk\AutoCAD_2024.1.5\AutoCAD 2024` | .NET Framework | **有** | — | net48 |
| 12 | R24.3.212.0.0 (2024.1.7) | `D:\Program Files\Autodesk\AutoCAD_2024.1.7\AutoCAD 2024` | .NET Framework | **有** | — | net48 |
| 13 | R24.3.236.0.0 (2024.1.9) | `D:\Program Files\Autodesk\AutoCAD 2024.1.9\AutoCAD 2024` | .NET Framework | **有** | — | net48 |
| 14 | R25.0.154.0.0 (2025.1.1) | `D:\Program Files\Autodesk\AutoCAD_2025.1.1\AutoCAD 2025` | **.NET 8** | 无 | ObjectARX-2025 | net8.0-windows |
| 15 | R25.1.74.0.0 (2026) | `D:\Program Files\Autodesk\AutoCAD 2026` | **.NET 8** | **有** | — | net8.0-windows |

### 1.1 被排除的命中（嵌套副本 / 第三方补丁目录）

以下命中**不作为测试目标**，仅登记以便审计：

| 目录 | 排除原因 |
|---|---|
| `D:\Program Files\Autodesk\AutoCAD_2024.1.5\AutoCAD 2024\crack` | 第三方补丁目录（`license_provenance=observed-third-party-patch-material`） |
| `D:\Program Files\Autodesk\AutoCAD 2026\破解补丁\*`（4 个子目录） | 第三方补丁目录（同上） |
| `D:\Program Files\Autodesk\AutoCAD 2018\Autodesk_AutoCAD_2018_1_2_Update_IO_Security_Hotfix_64bit_Subs` | 嵌套在宿主目录内，非独立安装 |

## 2. 许可与来源可信度（独立于安装事实）

CadBridge **未验证**任何宿主的许可状态。盘点脚本只记录观察结果：

| 观察 | 含义 |
|---|---|
| `license_provenance = unverified`（12 个宿主） | 安装来源未由本项目核实；不据此宣称正版或可用作发布证据 |
| `license_provenance = observed-third-party-patch-material`（2024.1.5 及其 2026 安装树的补丁子目录） | 存在第三方补丁材料。**该宿主在许可确认前不得作为发布验收证据目标**（ACCEPTANCE §1 要求证据可信） |

**结论（必须保持）**：安装数量多 ≠ 拥有可用测试许可。P1 选择测试目标前，须由项目所有者确认该宿主的许可可用于本项目验收；未确认的宿主只能在本地探索性使用，其证据必须标注 `LICENSE_UNVERIFIED`，不得进入 G01–G06 的放行证据。

## 3. 目标支持矩阵（对应 ACCEPTANCE §27 模板，展开为现场 build）

| 验证对象（现场可测） | 构建 | 基础 CAD (Query/Edit/Batch) | Lisp 运行 | 真 Debugger | 暂停 live query | 完整 V1 |
|---|---|---|---|---|---|---|
| 2016 `R20.1.107.0.0` / `R20.1.107.0.27` | NOT_RUN | NOT_RUN | NOT_RUN | EXPLORATORY | N/A | 未完成 |
| 2017 `R21.0.104.0.0` | NOT_RUN | NOT_RUN | NOT_RUN | EXPLORATORY | N/A | 未完成 |
| 2018 `R22.0.161.0.0` | NOT_RUN | NOT_RUN | NOT_RUN | EXPLORATORY | N/A | 未完成 |
| 2020 `R23.1.172.0.0` | NOT_RUN | NOT_RUN | NOT_RUN | EXPLORATORY | N/A | 未完成 |
| 2022 `R24.1.191.0.0` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | 未完成 |
| 2023 `R24.2.191.0.0` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | 未完成 |
| 2024 `R24.3.191.0.0` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | 未完成 |
| 2024 `R24.3.212.0.0` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | 未完成 |
| 2024 `R24.3.236.0.0` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | 未完成 |
| 2025 `R25.0.154.0.0` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | 未完成 |
| 2026 `R25.1.74.0.0`（≤Update1.1 / ≥Update1.2 待分列） | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | 未完成 |
| **2015** | **BLOCKED_NO_LOCAL_INSTALL** | BLOCKED | BLOCKED | BLOCKED | — | 未完成 |
| **2019** | **BLOCKED_NO_LOCAL_INSTALL** | BLOCKED | BLOCKED | BLOCKED | — | 未完成 |
| **2021** | **BLOCKED_NO_LOCAL_INSTALL** | BLOCKED | BLOCKED | BLOCKED | — | 未完成 |

**缺失年份记录规则**：`BLOCKED_NO_LOCAL_INSTALL` 表示本机未安装该年份，缺少可执行真机条件。这**不是** `unsupported`，也**不是** PASS。补齐方式由项目所有者决定（安装受许可宿主 / 缩小宣称范围 / 由他人执行该年份证据），不得由执行 AI 自行安装或改用邻近年份冒充。

**2026 Update 1.2 分列说明**：现场 2026 为 product version `R25.1.74.0.0`；UPDATE 级别需读取 `HKLM\SOFTWARE\Autodesk\AutoCAD\R25.1\ACAD-9101` 的补丁信息或产品 About 界面确认，P1 补做。2026 的 `.NET 10` 宿主变化（DESIGN S01）尚未在本机观察到：当前 `acdbmgd.runtimeconfig.json` 仍为 `net8.0`。

## 4. Debugger 能力结论（P0 阶段，仅文件级）

| 年份 | adapter 文件 | P0 结论 |
|---|---|---|
| 2016 / 2017 / 2018 / 2020 | 无 | **NOT_RUN / EXPLORATORY_PENDING**：需按 T01-6 探索是否存在受支持旧调试路径。文件缺失**不**等于官方不支持，也**不**等于不可用 |
| 2022 / 2024 / 2026 | 有 | **UNVERIFIED**：文件存在**不**表示 DAP 可用。必须由 T01-3 以真实 DAP client 完成 attach/断点/单步/栈/变量才能改判 |
| 2025 | 无 | **UNVERIFIED**：与 2026 同为 Modern 宿主却无 adapter 文件，需在 P1 确认官方调试范围与下载/安装前提 |

## 5. 待办（P1 前必须解决）

| ID | 事项 | 阻塞对象 |
|---|---|---|
| CM-1 | 项目所有者确认至少一个 Legacy（2024）与一个 Modern（2026）宿主的测试许可可用于验收 | G01/G02 证据有效性 |
| CM-2 | 确认 2025 官方 Debugger 分发方式（adapter 缺失原因） | A14/A19 |
| CM-3 | 确认 2026 UPDATE 级别并分列 1.1 / 1.2 | A19 |
| CM-4 | 2015/2019/2021 缺失年份的处置决定（安装 / 缩小范围 / 外部证据） | A19 完整宣称 |
| CM-5 | 决定测试宿主是否允许临时 NETLOAD（SECURELOAD / TRUSTEDPATHS 现场策略） | G01 |
