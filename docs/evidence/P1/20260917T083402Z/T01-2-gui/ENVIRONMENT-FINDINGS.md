# 环境发现：并发第三方活动与配置变更归因

**日期**：2026-09-17/18 · 证据：`docs/evidence/P0/20260917T083402Z/raw/cad-security-baseline.json`（before，16:36）
与 `docs/evidence/P1/20260917T083402Z/T01-2-gui/cad-security-baseline-AFTER.json`（after）

本文件记录在 P0→P1 期间对 AutoCAD 用户配置做的前后比对结果。**两项变更，归因不同。**

---

## 发现 1：本机存在**并发第三方活动**（高严重度）

`R24.2\ACAD-6101:804\Profiles\<<未命名配置>>\Variables\TRUSTEDPATHS` 在两次快照之间**增长**，
新增条目全部形如：

```text
E:\360data\TEMP\hosttest-original-control\stage\projects
E:\360data\TEMP\hosttest-p1-final\stage\projects
E:\360data\TEMP\hosttest-ctl-pal-20260917-175049\stage\projects
E:\360data\TEMP\hosttest-cand-accept-real3-20260917-215706\stage\projects
... （共数十条）
```

关键特征：

| 特征 | 值 |
|---|---|
| 命名模式 | `hosttest-*`、`hosttest-ctl-*`、`hosttest-cand-*`、`hosttest-v2-*` |
| 路径 | `E:\360data\TEMP\...`（**非** CadBridge 使用的 `F:\CadBridge-run\`） |
| 内嵌时间戳 | `20260917-175049`、`175328`、`180530`、`213949`、`214908`、`215706` |
| 涉及宿主 | `R24.2` = **AutoCAD 2023** |

**结论**：这些变更**不是 CadBridge 造成的**。CadBridge 从未：
- 修改任何 `TRUSTEDPATHS`；
- 操作 `R24.2`（AutoCAD 2023）配置；
- 使用 `E:\360data\TEMP\` 或 `hosttest-*` 命名。

时间戳（2026-09-17 17:50–21:57）落在 CadBridge 的 P0 盘点与 P1 构建之间，
表明**另一个进程/代理正在同一台机器上对 AutoCAD 2023 做加载实验**。

**影响**：
1. 本机不是受控实验环境；任何"前后比对"都可能混入第三方改动。
2. 兼容矩阵/配置类结论必须记录采集时刻，并在使用前重新校验。
3. 并发写入同一 AutoCAD 安装/配置会破坏 G01/G03 的可复现性。

**处置建议（需项目所有者决定）**：在开始需要独占环境的阶段（T01-2 PID 附加、T01-5 暂停点查询、
P3 写事务）之前，确认没有其他代理在操作同一宿主。

---

## 发现 2：CadBridge 的 GUI 测试**使 AutoCAD 把 `SECURELOAD=0` 落盘**（需记录）

`R24.3\ACAD-7101:804\Profiles\<<未命名配置>>\Variables\SECURELOAD`：**无值 → `0`**。

归因分析：

| 事实 | 说明 |
|---|---|
| CadBridge 代码从未写入 `SECURELOAD` | 插件只读 `Database`/`LayerTable`/`Transaction`，不触碰注册表或系统变量 |
| 变更发生在启动 AutoCAD 2024.1.7 **GUI** 之后 | before 快照 16:36 无值；after 快照在两次 GUI 会话之后 |
| `R25.1`（AutoCAD 2026）**未变化** | 它在 before 快照中**本来就**是 `SECURELOAD=0` |

**最可能的机制**：该安装的默认 profile 中 `SECURELOAD` 本就是 0（本机多个 profile 均为 0，
见 P0 基线）。首次以该 profile 启动 GUI 时，AutoCAD 把 profile 变量**物化**写入 HKCU，
于是 `SECURELOAD=0` 从"未落盘"变为"已落盘"。

**这仍必须如实记录，因为**：
- A01 要求"不降低 SECURELOAD"。CadBridge 未主动降低，但**测试会话的副作用**使该值被持久化。
- 无法在事后区分"AutoCAD 物化了既有默认值"与"某个更早的第三方补丁设置过它"。
- **CadBridge 不会**为"修复"它而写注册表（那是又一次未授权修改）。

**A01 影响**：本项**不能**判定为"CadBridge 未改动配置"的干净通过。
应记为：`SECURELOAD` 在测试后于 R24.3 profile 中为 `0`，与 R25.1 等既存 profile 一致；
需项目所有者确认该值是否符合预期，并决定是否在正式验收前恢复。

---

## 发现 3：`SECURELOAD` 阻挡负例在本机不可复现

由于本机多个 profile 的 `SECURELOAD` 既存为 `0`（P0 基线已记录），
A01/A18 要求的"策略阻挡加载 → `LOAD_BLOCKED_BY_POLICY`"负例**无法自然复现**。
CadBridge **不会**为制造该负例而修改设置。

**状态**：`BLOCKED`（需在独立受控环境验证），不得伪造。

---

## 复算方法

```bash
# before（已在 P0 采集）
powershell -File tools/capture-cad-security-baseline.ps1 \
  -OutFile docs/evidence/P0/20260917T083402Z/raw/cad-security-baseline.json

# after
powershell -File tools/capture-cad-security-baseline.ps1 \
  -OutFile docs/evidence/P1/20260917T083402Z/T01-2-gui/cad-security-baseline-AFTER.json

# 比对
python tools/compare-security-baseline.py <before.json> <after.json>
```
