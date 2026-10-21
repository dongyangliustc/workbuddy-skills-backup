# Q-Chem 模块选择表（任务 → 关键字配方）

> **所有配方均标注依据**：`[M:p.xxx]` = Q-Chem 6.1 手册页码；`[S:path]` = 随 6.1.1 发布的官方样例。
> 表中未列的写法**不要凭记忆使用** —— 先用 `qc_kb.py search/keyword` 核实。

## 0. 先确定「版本」——这一步错则全错

| 源 | 版本 | 角色 |
|---|---|---|
| 本机 `$QC/doc/manual.pdf` + `$QC/samples/` | **6.1.1** | **判定基准**：能不能在本机跑，以它为准 |
| 用户离线 PDF（`qchem_manual.pdf`，1816 页） | 6.2 | 阅读、拓展现有认知 |
| 线上 https://manual.q-chem.com/latest/ | **7.0** | 交叉核对；⚠️ **可能含 6.1.1 没有的关键字/方法** |

> ⚠️ 线上手册版本（7.0）高于本机（6.1.1）。**线上查到的关键字必须回到 6.1.1 知识库复核**，
> 否则可能写出本机不认的输入。本知识库的 `validate` 默认即以 6.1.1 为准。

## 1. 任务 → 模块配方

| 任务 | 关键配方 | 依据 |
|---|---|---|
| **单点能** | `JOBTYPE SP` + `METHOD` + `BASIS` | `[S:dft/*.in]`、`kb/values_observed.json` |
| **几何优化** | `JOBTYPE OPT` + `GEOM_OPT_TOL_{GRADIENT,DISPLACEMENT,ENERGY}` | `[S:ccman/EEEOMCCSD_opt.in]` `[S:ccman/CCSD_opt.in]` |
| **频率/振动（含 IR 强度）** | `JOBTYPE FREQ`。**IR 强度（km/mol）与 `TransDip` 默认输出，无需关键字**；Raman 强度需 `DORAMAN TRUE`；非谐用 `ANHAR TRUE` + `VCI n` | `[S:freq/]` `[M:p.972-973]` `[M:p.1000]` |
| **高精度单点** | `METHOD CCSD(T)` + `CC_CONVERGENCE` + `MAX_SCF_CYCLES` + `N_FROZEN_CORE` | `[S:ccman/CCSD_opt.in]` `[S:ccman/CC_FNO.in]` |
| **激发能（波函数）** | `METHOD EOM-CCSD` + **`EE_STATES [n1,n2,n3,n4]`** | `[S:ccman/EEEOMCCSD_openshell.in]` |
| **激发能（廉价替代）** | **`EXCHANGE` + `CORRELATION` + `CIS_N_ROOTS`**（⚠️ **没有** `METHOD TDDFT` 这种写法） | `[M:p.452 Example 7.21]` |
| **自旋翻转（双自由基/键断裂）** | `SPIN_FLIP 1` + `CIS_N_ROOTS` +（可选 `$xc_functional` 自定义） | `[M:p.454 Example 7.23]` |
| **溶剂化（PCM）** | `$pcm` / `$solvent` 段，或 `N_SOL` 等 | `[M:p.453 Example 7.22]` `[S:solvent/]` |
| **EDA 能量分解** | `JOBTYPE EDA` | `[S:eda/]`、实测取值 |
| **极化率/（超）极化** | `JOBTYPE POLARIZABILITY` | `[S:polarizability/]` |
| **基组重叠误差** | `JOBTYPE BSSE` | 实测取值 |
| **AIMD / 实时 TDKS** | `JOBTYPE AIMD`；实时 TDKS 用 `TDKS true` + `$tdks` 段 | `[S:aimd/]` `[S:tddft/td_rks_h2.in]` |

## 2. 方法（METHOD）选择——按官方样例实测频次（`kb/values_observed.json`）

在一模一样的 **6.1.1 官方样例**里，METHOD 的实测使用次数（前 8）：

| METHOD | 次数 | 适用场景 |
|---|---|---|
| `EOM-CCSD` | 188 | 激发态/电离/电子亲和（本项目主用） |
| `HF` | 128 | 基线、初猜、大体系预扫 |
| `B3LYP` | 100 | 常规 DFT 几何优化/能量 |
| `CCSD` | 56 | 闭壳层关联能 |
| `ADC(2)` | 30 | 激发态替代方案（比 EOM-CCSD 便宜） |
| `ADC(3)` | 22 | 同上，精度更高 |
| `EOM-MP2` | 20 | 大体系激发态粗筛 |
| `MP2` | 16 | 关联能基线 |

> **这是"下界而非全集"**：样例没用过不等于不支持；但**样例用过必然支持**，故优先选表中的。
> 查别的关键字用 `qc_kb.py values <KEYWORD>`。

## 3. 基组（BASIS）选择——同源实测频次

`6-31G*`(161) · `STO-3G`(137) · `cc-pVDZ`(115) · `6-31G`(86) · `3-21G`(56) · `6-31+G*`(45) ·
`cc-pVTZ`(38) · `aug-cc-pVDZ`(31) · `aug-cc-pVTZ`(14) · `GENERAL`(19，配合 `$basis` 段自定义)

实践口径：
- 链长扫描/预筛：`6-31G*` 或 `cc-pVDZ` + TDDFT
- 生产激发能：`aug-cc-pVTZ`（阴离子/里德堡态需要弥散函数，**aug-** 不能省）+ EOM-CCSD
- 自定义/ECP：`BASIS GENERAL` + `$basis` / `$ecp` 段

## 4. 与 HPCCube 平台约束的耦合（必须一起考虑）

| 约束 | 做法 | 依据 |
|---|---|---|
| sbatch 硬校验 `内存/CPU ≤ 3944MB` | `--cpus-per-task = ceil(内存GB*1024/3944)` | 见 remote-sync SKILL.md |
| Q-Chem 默认按**整节点内存**申请堆 → 被 cgroup OOM 杀 | **必须**在 `$rem` 显式写 `MEM_TOTAL`（略小于 `--mem`），CC 类方法再加 `CC_MEMORY` | 实测 exit 137 / `OUT_OF_MEMORY` |
| 平台默认 scratch 为组共享目录 | `QCSCRATCH` 指向任务目录（gtransfer 已自动处理） | 实测并发作业互相覆盖 |
| 被 kill 的作业留下损坏 `qarchive.h5` | 每轮先清空 scratch，第三参数带 jobid | 实测启动即失败 |
| Q-Chem 串行版（`QCMPI=seq`） | 并行靠**多作业并发**，`--cpus-per-task` 只为换内存 | 实测无 OpenMP |

## 5. 常见幻觉写法对照（一律禁止）

| ❌ 幻觉写法 | ✓ 正确写法 | 出处 |
|---|---|---|
| `METHOD TDDFT` | `EXCHANGE` + `CORRELATION` + `CIS_N_ROOTS` | `[M:p.452]` |
| `EOM_NROOTS` / `N_EXCITED` | `EE_STATES`（EOM-CCSD）/ `CIS_N_ROOTS`（CIS/TDDFT） | `[S:ccman/EEEOMCCSD_openshell.in]` |
| `BASIS_SET` | `BASIS` | `qc_kb.py keyword BASIS` |
| `JOBTYPE EXCITATION` | `JOBTYPE SP` + 激发态关键字；激发态**不是** JOBTYPE | `kb/values_observed.json: JOBTYPE` |
| `MEMORY 20000` | `MEM_TOTAL`（MB）/ `CC_MEMORY` | `qc_kb.py keyword MEM_TOTAL` |

> 上表每一条都可以用 `qc_kb.py validate <file>` 自动拦下 —— 该命令对未知关键字**报错并给出候选**。

## 6. 推荐工作流（模块选择 + 模板 + 校验 + 提交）

```bash
SK=~/.workbuddy/skills/qchem-agent

# ① 选模块：查任务对应配方（本文件）+ 核实关键字
python $SK/scripts/qc_kb.py search "excitation energy EOM-CCSD open shell"
python $SK/scripts/qc_kb.py keyword EE_STATES
python $SK/scripts/qc_kb.py values METHOD

# ② 取模板 → 改体系/基组/内存
cp $SK/references/templates/05_eom_ccsd_excitation_openshell.in ./C6N_eom.in

# ③ 过校验闸门（未知关键字直接报错，退出码 2）
python $SK/scripts/qc_kb.py validate ./C6N_eom.in

# ④ 按内存反推核数并提交（MEM_TOTAL 必须 <= --mem）
#    100GB 内存 ⇒ 26 核；见 remote-sync 的 hpccube profile
python ~/.workbuddy/skills/remote-sync/scripts/gtransfer.py submit \
  --server hpccube --local ./C6N_eom.in --remote "~/C2nCN/c6n_eom" \
  --mem 100G --cpus-per-task 26 --walltime 7-00:00:00 \
  --download-glob "*.out *.fchk" --out-dir ./results/c6n_eom --timeout 604800
```
