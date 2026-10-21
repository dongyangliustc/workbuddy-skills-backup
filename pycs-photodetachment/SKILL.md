---
name: pycs-photodetachment
description: 在课题组服务器上调用 PYCS 计算阴离子光脱附截面（PDCS），含弥散基组 even-tempered 阶梯收敛流程。当需要用 Gaussian 阴离子 .fchk 跑光脱附/光电离截面，或需要为 PDCS 收敛而增补弥散基函数时使用。不适用于激发态、反应网络、速率常数计算。
agent_created: true
---

# PYCS 光脱附截面计算（课题组服务器）

## 何时使用

- 需要用 Gaussian 生成的阴离子 `.fchk` 计算光脱附截面（PDCS / photodetachment cross section）；
- 需要判断阴离子波函数的弥散基组是否足够（以 PDCS 收敛为准的 even-tempered 阶梯）；
- 需要解析 PYCS 输出的三套规范（length / moment / mix）与 β 参数。

## 服务器环境（2026-09-01 实测，勿凭记忆改动）

| 项目 | 实测值 |
|---|---|
| 主机/用户 | __SERVER_IP__（hostname `login`），user `ldy`；凭据见 `~/.workbuddy/transfer.conf` 的 `[server:default]` |
| 程序目录 | `/opt/app/PYCS`（v1.3，module 封装）、`/opt/app/PYCS_V2`（重构版，**推荐**） |
| module | `module load PYCS/pycs_v1.3` → `PYCS_ROOT=/opt/app/PYCS`、`PYCS_MAIN=$PYCS_ROOT/main.py`、别名 `pycs`、自动加载 `python/3.10` |
| conda | `base`、`amk`、`pycs-sw`（`/home/ldy/miniconda3/envs/pycs-sw`） |
| 自检算例 | `/opt/app/PYCS_V2/` 下 `Li-.fchk`、`input.txt`、`Li.out`、`Li-benchmark.out` |

默认 PATH 中**没有** `pycs` 命令，必须 `module load` 或直接 `python <path>/main.py`。

## 调用方式

```bash
# V2（推荐）
source ~/miniconda3/etc/profile.d/conda.sh
conda activate pycs-sw        # 若依赖装在 base 则省略；需 Li- 基准实测确认
python /opt/app/PYCS_V2/main.py -p <cores> input.txt

# v1.3（module 封装）
module load PYCS/pycs_v1.3
pycs -p <cores> input.txt
```

CLI（两版一致，V2 由 argparse 定义）：`-p, --processes <int>`（默认 1）、`--max-processes`、位置参数 `input_file`。

## 输入文件格式（V2 `PhotoConfig`）

```text
fchk_file_path = anion.fchk
degeneration = 1
ionization_energy = 0.618
energy_range_lower_limit = 0.01
energy_range_higher_limit = 5.00
energy_num = 51
integral_method = 1
Lebedev_point_num = 50
Schmidt = False
ionized_electron_type = alpha
out_file = run.out
ionized_orbital = 2
open_shell = False
```

键说明：`ionized_orbital` 为**1-based** MO 序号（从 `.fchk` 占据轨道数读取，不得猜）；`integral_method` 1=Lebedev、2=Average；`degeneration` 为电离轨道简并度（按谱项定，不默认 1）；`open_shell` 闭壳层 False；出现 `dyson_list` 即自动触发 Dyson 轨道重组（可为内联数值或 `.txt` 文件）。

### 三个会静默失效的坑

1. **必须写 `键 = 值`（等号两侧留空格）**：解析器 `line.split()`，长度 < 3 的行被**静默跳过**；写成 `key=value` 参数不生效且不报错。
2. **v1.3 与 V2 键名不同**：v1.3 用 `Schimt`（拼写）且需显式 `perform_dyson`；V2 用 `Schmidt` 且 `dyson_list` 自动触发。**两版禁止混用**。
3. **能量网格相对 IP 定义**：`linspace(IP + lower, IP + upper, num)`。做基组收敛对比时，必须把 `ionization_energy` 固定为同一个外部生产值（本项目取 CCSD(T) 的 **ADE**），否则网格随 IP 漂移 → 伪收敛。

## 输出解析

输出文本含 6 个数据块，按表头字符串定位：

1. `cross section results in length gauge ...` — `energy(eV) cross_tot cross_para cross_perp`（a.u.）
2. `cross section results in moment gauge ...` — 同上
3. `cross section results in mix gauge ...` — 同上
4. `beta parameters (for e=ip, beta is meaningless)` — `energy(eV) beta_length(m) beta_moment beta_mix`
5. `final cross section results ...` — `energy(eV) length(A^2) moment(A^2) mix(A^2) unit is a.u.`
6. `energy(eV) cross_length(Mb) cross_moment(Mb) cross_mix(Mb) unit is Mb`

单位常数：v1.3 `au2mb = 28`；V2 `OutputWriter.AU2MB = 28.0`（精确值 28.0028，仅影响绝对标度）。判据序列通常取第 6 块的 `cross_mix(Mb)` 列。

## 限制（记录，勿绕过）

V2 连续态当前只有 **PlaneWave**（`continuum_wave.py`；`main.py` 中 `wave_model = PlaneWave()`，源码注释预留 CoulombWave 但未实现）。近阈值绝对 PDCS 缺 Coulomb/正交化修正 → 可用于**相对收敛判定**，不可直接作发表级绝对截面。

## 标准工作流（弥散基阶梯 + PDCS 收敛）

1. **基准门（必做）**：用 `/opt/app/PYCS_V2/` 自带 `Li-.fchk` + `input.txt` 复跑，输出与 `Li-benchmark.out` 比对一致才继续。不过则回报，不自行换版本。
2. **弥散阶梯**：起始基组 **aug-cc-pVTZ（AVTZ）**，even-tempered 增补：对每个角动量 L，取该原子该 L 最小指数 α_min(L)，按 α_min·β^(−k) 添加，k=1…n，β=2.0；n 从 0 递增（通常 n_conv ≈ 2–3）。
3. **逐级监控**：HOMO 能 ε、⟨r²⟩^½、r = 10/20/30 a₀ 径向尾幅、ADE/EA、PDCS（近阈值点 + 平台区点）。
4. **终止判据（n 与 n−1 比较）**：|Δε| < 0.001 eV 且 |Δ⟨r²⟩^½| < 0.5% 且 |ΔADE| < 0.01 eV 且 |Δσ/σ| < 1%（近阈值与平台区同时满足）。
5. **护栏**：SCF 不收敛、近线性依赖、或 ε_HOMO 由负转正（电子自脱附）→ 立即停止上报，不得继续加弥散"救活"。
6. **落盘**：每级保留 `.fchk` + Gaussian `Gen` 基组块（`.gbs`）+ `.log` + PYCS 输入全文与输出全文，保证可复现。

## REA 速率库批次的固定约定

- `ionization_energy` 取 **ADE**（= EA_0 = ZPE 校正后的绝热脱附能，CCSD(T)/aug-cc-pVQZ 级），**不用 VDE**；全阶梯固定同一个外部生产值（否则网格随 IP 漂移 → 伪收敛）。
- DFT 频率须乘 ZPE 校正因子 λ：按 model chemistry 从 NIST CCCBDB "Vibrational scaling factors" 检索，记录数值+来源+检索日期，**禁止记忆值**；未校正值同时保留。
- 角向网格 `Lebedev_point_num` 固定 **50**，不做角向收敛测试；所得 PDCS 必须表述为"50 点角向网格下的值"，不得称为完全收敛的绝对截面。
- 能量网格默认 lower = 0.01 eV、upper = 5.00 eV、`energy_num` = 51。
- 工作流三段并逐段授权：Part A 结构优化 → Part B CCSD(T) 单点能校正 → Part C PYCS 计算 PDCS。

## 无 paramiko 时的远程探测

Windows 沙箱默认无 `sshpass`、系统 Python 常无 `paramiko`。用托管 venv 安装后探测（不污染系统环境）：

```bash
PY="C:\Users\<user>\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
"$PY" -m pip install -q paramiko
```

探测脚本模式：读 `~/.workbuddy/transfer.conf` 取凭据 → `paramiko.SSHClient()` + `AutoAddPolicy` → `exec_command` 批量执行只读命令（`ls` / `cat` / `sed -n` / `module show`），一次性把程序路径、module 文件、输入样例、输出样例全部取回。**先实测再写入任何规范文档，绝不凭记忆编写调用命令。**
