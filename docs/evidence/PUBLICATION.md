# 发布与证据完整性说明（Publication & Evidence Integrity）

本文件说明：这个仓库在**首次公开发布**时对原始本地目录做了哪些处理，为什么这样做，
以及如何独立验证。它是一份**如实披露**，不是"已清理干净"的声明。

---

## 1. 可回退基线（回退目标）

| 项目 | 值 |
|---|---|
| 仓库 | <https://github.com/moshouhot/CadBridge> |
| 默认分支 | `main` |
| 审计前基线 commit | `592c6ff` (`Initial public baseline: CadBridge architecture, P0/P1 evidence, tooling`) |
| 审计前基线 tag | `baseline/pre-sourcery-audit`（annotated） |
| 审计工作分支 | `audit/sourcery-baseline` |
| 仓库外备份 | `F:\CadBridge-baseline-backup\CadBridge-pre-sourcery-20260919T231716\` |
| 备份校验清单 | 该目录下 `MANIFEST.sha256`（258 条），其自身 SHA-256 记录在审计报告中 |

回退方法：

```bash
git fetch --tags origin
git checkout baseline/pre-sourcery-audit     # 回到审计前的完整状态
```

---

## 2. 首次发布时做的处理（逐项披露）

### 2.1 移除本机账号名（4 个证据文件）

原始证据里有 4 个文件包含本机 Windows 账号名（`<本机账号>`，同时暴露机器名）。公开仓库
不应当包含它，因此把这些字面标识符替换为 `<REDACTED-USER>`：

| 文件 | 原始 SHA-256 | 发布后 SHA-256 |
|---|---|---|
| `docs/evidence/P0/20260917T083402Z/raw/dotnet-info.txt` | `a7150bea…` | 见该 manifest 的 `redactions` 块 |
| `docs/evidence/P1/20260917T083402Z/T01-1/build/modern-build.log` | `30a20825…` | 同上 |
| `docs/evidence/P1/20260917T083402Z/T01-1/matrix/2016-legacy.txt` | `c80641c1…` | 同上 |
| `docs/evidence/P1/20260917T083402Z/T01-1/matrix/2016-modern.txt` | `b8d30710…` | 同上 |

**改动的边界（重要）**：只替换了机器路径里的账号名字符串。**没有**改动任何测量值、退出码、
CAD 输出、结论或状态。每个 manifest 的 `redactions` 块记录了原始哈希、发布后哈希和替换规则，
可用仓库外的备份逐字节复核。

> **标识符策略**：被脱敏的字面标识符本身**不再出现在本仓库任何位置**（包括 provenance 块），
> 因为仓库是公开的。原始字节与原始哈希保存在**仓库外**的备份中。

### 2.2 忽略 AutoCAD 运行时垃圾（不是证据）

仓库根目录在真机测试时被写入了 45 个宿主控制台日志（`acad.err`、`Drawing1_*.log`）和一个
临时脚本目录 `CadBridge-run_*/`。它们不是证据，已在 `.gitignore` 中排除：

```
/acad.err
/Drawing1_*.log
/CadBridge-run_*/
```

真正的证据在 `docs/evidence/**`，有 manifest 与哈希。

### 2.3 证据哈希与 Git 字节一致性（`core.autocrlf` 陷阱）

**这是一个真实存在的完整性缺陷，已修复。**

本仓库创建于 `core.autocrlf=true` 的 Windows 环境。该设置会在**存储 blob 时把 CRLF 改写为
LF**，于是全新 clone 得到的字节与 `manifest.json` 里记录的 SHA-256 不一致 —— 证据校验会
毫无理由地失败，"验证证据"这个动作就失去意义。

实测证据（`docs/evidence/P0/20260917T083402Z/raw/os.json`）：

```
工作区字节 = 204   CR 数 = 7
blob  字节 = 197   CR 数 = 0     <-- 字节被改写了
```

修复方式：新增 `.gitattributes`，对**字节本身就是证据**的路径关闭 EOL 转换：

```
docs/evidence/**  -text
*.jsonl           -text
*.scr             -text        # NETLOAD 脚本，行尾与末尾换行是语义的一部分
```

其余文件按类型处理（`*.sh` 强制 LF，`*.ps1/*.cmd` 强制 CRLF，`*.dll/*.dwg` 为 binary）。

### 2.4 从公开上传中排除的证据文件

`.gitignore` 排除了两个 P0 原始 artifact，它们包含本机主机名与完整安装清单：

| 文件 | 大小 | 排除原因 |
|---|---|---|
| `docs/evidence/P0/20260917T083402Z/raw/autocad-inventory.json` | ~107 KB | 含主机名 `TIGER5800` 与本机全部 AutoCAD 安装路径清单 |
| `docs/evidence/P0/20260917T083402Z/raw/running-cad-processes.json` | 3 字节（`[]`） | 本机运行进程清单（内容为空，仍按同类处理） |

**如实披露**：P0 的 `manifest.json` 仍然把这两个文件列为 artifact 并记录了它们的哈希，因此
在公开仓库里这两条记录**无法被验证**（文件不在仓库中）。原始文件保存在仓库外的备份里。
这是有意为之的取舍：与其为了"manifest 全绿"而公开本机主机名，不如保留 manifest 的历史记录
并在此处明确说明。

### 2.5 首次发布的历史提交中曾泄漏标识符（已处理）

**这是一次真实的、由本次工作自身造成的泄漏，已修复，并在此如实记录。**

首次 push 的基线提交里，我为脱敏写的 provenance 元数据字段 `replacement` **把被脱敏的
字面账号名又写了回去**，导致该标识符出现在 `main` 的历史提交中（2 个 manifest 文件，共 6 处）。
换句话说，脱敏动作本身泄漏了它想隐藏的字符串。

处理方式与依据：

1. 发布文件中的该字段已改为不包含标识符的描述（`local account name -> <REDACTED-USER>`）。
2. 由于该仓库在发现时**创建仅数分钟、0 fork、0 star、0 watcher、0 协作者、0 PR**，
   重写这一个提交的历史不存在协作代价，因此**重写了历史**并 force-push，使该标识符
   在 `main` 与审计分支的任何提交中都不再存在。重写前 `main` = `7242a86`，重写后
   `main` = `592c6ff`。
3. 重写后的基线提交与原提交的差异**仅限**：provenance 元数据字段不再包含标识符、
   新增 `.gitattributes`、以及因该文件生效而恢复的证据文件原始字节。代码与证据内容未变；
   `baseline/pre-sourcery-audit` 标签已指向新提交（重写前 `3d75e92` → 重写后 `bd903c9`）。
4. 验证：对 `HEAD` 的全部 208 个 blob 做二进制安全的模式扫描，标识符与常见凭据模式
   均无命中；145 个证据 artifact 的 SHA-256 与 manifest 一致（仅 §2.4 中 2 个有意排除
   的文件不在仓库内）。

> 如果后续发现该标识符仍存在于任何历史提交、GitHub 缓存或镜像中，按此节的说明处理；
> 不要声称"历史中从未出现过"。旧提交 `7242a86` / `3d75e92` 已从远程分支与标签中移除，
> 但它们可能在一段时间内仍可通过 GitHub 的悬空对象（dangling object）按 SHA 直接访问。

### 2.6 该标识符又泄了两次，现已改为强制校验（自我披露）

**重要**：上面 §2.5 的修复**并不完整**，随后又发生了两次同类泄漏：

1. 在审计分支上新增的 `docs/evidence/P2/.../build-modern.txt`（构建日志）里，MSB3277 警告
   带出了 `C:\Users\<本机账号>\.nuget\...`，共 24 处。
2. 在修复第 1 次泄漏时，发现 **P1 的两个 `.raw` 证据文件**（`2016-legacy.raw`、
   `2016-modern.raw`，UTF-16 编码）里也各有一处，而这两个文件是 **manifest 引用**的证据。

三次泄漏都指向同一个根因：**证据是真实命令在真实主机上跑出来的，机器路径天然会带出账号名**。
所以手工清理不是修复。现在的做法是：

- 新增 `tools/redact-evidence.py`：按 `--identifier`（或环境变量 `CB_REDACT_IDENTIFIER`）
  扫描并脱敏，**同时写出 `<file>.redaction.txt` 旁注**（记录原始 SHA-256、存储后 SHA-256、
  命中次数、替换范围），使改动可审计而非隐形。它**刻意不把标识符写死在代码里** ——
  这个文件本身是公开的，写死等于重新发布要删的字符串。
- `tools/selftest.sh` 新增**不变式检查**：只要任何被跟踪文件里出现该标识符，套件即失败
  （标识符经环境变量传入，未设置时该项显示 `SKIP` 而不是假 `PASS`）。
- 受影响的 manifest 已更新哈希，并在 `redactions` 块里逐条记录原始/存储哈希；
  P1 两个 `.raw` 文件与 P2 构建日志均已如此处理。

**当前状态（可复核）**：`git ls-files` 范围内的所有文件均无该标识符；158 个证据 artifact 的
SHA-256 与其 manifest 一致（仅 §2.4 中 2 个有意排除的文件不在仓库内）。

---

## 3. 如何独立验证

### 3.1 验证备份与原始字节

```bash
# 在备份目录中（仓库外）
cd /f/CadBridge-baseline-backup/CadBridge-pre-sourcery-20260919T231716
sha256sum -c MANIFEST.sha256
```

### 3.2 验证已发布的证据哈希（对照**已提交的 blob**，而不是工作区）

必须校验 blob，因为工作区字节可能被 EOL 转换影响：

```bash
python - <<'PY'
import subprocess, json, hashlib, pathlib
bad = ok = 0
for m in pathlib.Path("docs/evidence").rglob("manifest.json"):
    d = json.loads(m.read_text(encoding="utf-8"))
    prefix = m.parent.as_posix()
    for a in d.get("artifacts", []):
        rel = f"{prefix}/{a['path']}"
        r = subprocess.run(["git", "cat-file", "blob", f"HEAD:{rel}"], capture_output=True)
        if r.returncode != 0:
            print("NOT IN COMMIT:", rel); bad += 1; continue
        if hashlib.sha256(r.stdout).hexdigest() != a["sha256"]:
            print("MISMATCH:", rel); bad += 1
        else:
            ok += 1
print(f"ok={ok} problems={bad}")
PY
```

**预期**：除 §2.4 中被有意排除的 2 个文件外，其余全部匹配。

### 3.3 确认没有凭据与本机标识符被提交

对**已提交的 blob**（包含非 UTF-8 证据）做模式扫描，而不是只扫工作区文本文件。

本机标识符的专用检查：

```bash
CB_REDACT_IDENTIFIER="<本机账号名>" python tools/redact-evidence.py --check
```

该检查已集成到 `tools/selftest.sh`，未设置环境变量时会显示 `SKIP`（**不是** `PASS`）。

---

## 4. 未做声明（明确不声称的事）

- 不声称"本仓库不含任何本机信息"：机器路径（`D:\CAD APPLOAD\...`、`F:\CadBridge-run\...`）
  与 AutoCAD 安装位置仍在证据中，因为**附加正确性依赖于这些路径**，且它们不是凭据。
- 不声称历史提交已彻底清理：本仓库的**首次**基线提交（`7242a86`）曾包含被脱敏的标识符。
  该标识符已通过历史重写（`7242a86` → `592c6ff`）从 `main` 与标签中移除，当前全部 blob
  已扫描确认无命中；但旧提交对象在 GitHub 侧可能短期内仍可按 SHA 访问，详见 §2.5。
- 不声称证据已通过真机重跑验证：本次工作**没有**启动任何 AutoCAD / CoreConsole 会话。
