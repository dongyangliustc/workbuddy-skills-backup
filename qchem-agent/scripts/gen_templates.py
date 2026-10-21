#!/usr/bin/env python3
"""生成 Q-Chem 通用模板（每次模板改动后重跑，保证 LF 行尾与格式统一）。

设计约束
--------
- 只使用**本版本知识库中可溯源**的 $rem 关键字（生成后由 qc_kb.py validate 强制校验）。
- 每个模板的 $comment 里写明配方来源：手册示例页码 或 官方样例路径。
- 坐标/电荷/多重度留占位符并给出可运行的默认小体系，便于直接冒烟。
- 显式给出 MEM_TOTAL / CC_MEMORY：这是 HPCCube 上的**硬要求**
  （Q-Chem 默认按整节点内存申请堆 → 被 SLURM cgroup OOM 杀），
  见 ../../SKILL.md 的「HPCCube 运行约束」。
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "references" / "templates"
OUT.mkdir(parents=True, exist_ok=True)

H2O = """O     0.0000000000    0.0000000000    0.0000000000
H     0.0000000000    0.0000000000    0.9572000000
H     0.9227000000    0.0000000000   -0.2575000000"""

CH3 = """C     0.0000000000    0.0000000000    0.0000000000
H     0.0000000000    0.0000000000    1.0790000000
H     0.9346000000    0.0000000000   -0.3596000000
H    -0.4673000000   -0.8093000000   -0.3596000000"""

TEMPLATES: dict[str, str] = {}

TEMPLATES["01_sp_energy.in"] = f"""$comment
单点能（SP）—— 最基础的模板。
配方来源：手册 6.1 Example 段（$rem 通用最小集）；关键字 JOBTYPE/BASIS/MEM_TOTAL
均为官方手册定义 + 官方样例在用（见 kb/keywords.json）。
$end

$molecule
0 1
{H2O}
$end

$rem
JOBTYPE          SP
METHOD           B3LYP
BASIS            6-31G*
MEM_TOTAL        8000        ! 必填：须 <= SLURM --mem（MB）
$end
"""

TEMPLATES["02_opt_geometry.in"] = f"""$comment
几何优化（OPT）。
配方来源：官方样例 ccman/EEEOMCCSD_opt.in 与 ccman/CCSD_opt.in 的 JOBTYPE/收敛关键字；
GEOM_OPT_TOL_* 取值口径同官方样例。
$end

$molecule
0 1
{H2O}
$end

$rem
JOBTYPE                  OPT
METHOD                   B3LYP
BASIS                    6-31G*
GEOM_OPT_TOL_GRADIENT    3      ! 收敛限（3 为常用档）
GEOM_OPT_TOL_DISPLACEMENT 3
GEOM_OPT_TOL_ENERGY      4
MEM_TOTAL                8000
$end
"""

TEMPLATES["03_freq_vibrational.in"] = f"""$comment
频率/振动分析（FREQ）—— 用于确认驻点性质与零点能。
配方来源：官方样例 freq/ 目录；关键字 JOBTYPE=FREQ 见 kb/values_observed.json。
$end

$molecule
0 1
{H2O}
$end

$rem
JOBTYPE          FREQ
METHOD           B3LYP
BASIS            6-31G*
MEM_TOTAL        8000
$end
"""

TEMPLATES["04_ccsdt_single_point.in"] = f"""$comment
CCSD(T) 高精度单点能（本课题组既有 C12H- 计算用的就是这套口径）。
配方来源：官方样例 ccman/CCSD_opt.in、CC_FNO.in；
内存/收敛关键字 MEM_TOTAL / CC_MEMORY / CC_CONVERGENCE / SCF_CONVERGENCE
均为手册定义（kb/keywords.json 内可查页码）。
$end

$molecule
0 1
{H2O}
$end

$rem
JOBTYPE            SP
METHOD             CCSD(T)
BASIS              aug-cc-pVTZ
SCF_CONVERGENCE    8          ! SCF 收敛限（10^-8）
CC_CONVERGENCE     6          ! CC 振幅收敛限（10^-6）
MAX_SCF_CYCLES     100
N_FROZEN_CORE      1          ! 冻结核心轨道数（FC=auto，亦可给整数）
MEM_TOTAL          28000      ! 须 <= SLURM --mem
CC_MEMORY          20000      ! CC 模块内存，须 < MEM_TOTAL
$end
"""

TEMPLATES["05_eom_ccsd_excitation_openshell.in"] = f"""$comment
开壳层 EOM-EE-CCSD 激发能 —— 本项目（氰基多炔烃自由基 C2nCN）的主用模板。
配方来源：官方样例 ccman/EEEOMCCSD_openshell.in（同为 charge=0, mult=2 开壳层），
关键点：激发态用 **EE_STATES [irrep 各不可约表示的态数]** 指定，不是 "N_ROOTS"。
$end

$molecule
0 2
{CH3}
$end

$rem
JOBTYPE                    SP
METHOD                     EOM-CCSD
BASIS                      aug-cc-pVTZ
N_FROZEN_CORE              0        ! 开壳层 + 小体系：显式给 0 更可控
SCF_GUESS                  SAD
EE_STATES                  [0,0,0,1]  ! 4 个不可约表示各取几个态（C1 时用最后一个）
EOM_NGUESS_SINGLES         2
EOM_DAVIDSON_CONVERGENCE   9
CC_T_CONV                  9
MEM_TOTAL                  28000    ! 须 <= SLURM --mem
CC_MEMORY                  20000
$end
"""

TEMPLATES["06_tddft_excitation.in"] = f"""$comment
TDDFT（线性响应）激发能 —— EOM-CCSD 的廉价替代，用于链长扫描与初筛。
配方来源：手册 Q-Chem 6.1 Chapter 7 **Example 7.21（p.452）**，开壳层甲基过氧自由基。
⚠ 关键（最易记错）：TDDFT 用 **EXCHANGE + CORRELATION + CIS_N_ROOTS** 触发，
   **没有** METHOD TDDFT 这种写法。
  RPA=FALSE（默认）为 TDA；RPA=TRUE 为完整 TDDFT。
$end

$molecule
0 2
{CH3}
$end

$rem
EXCHANGE            b            ! 或 b3lyp / pbe0 等（见 kb/values_observed.json: EXCHANGE）
CORRELATION         lyp          ! 与 EXCHANGE 配对使用
CIS_N_ROOTS         5
BASIS               6-31+G*
SCF_CONVERGENCE     7
MEM_TOTAL           8000
$end
"""

TEMPLATES["07_sftddft_excitation.in"] = f"""$comment
自旋翻转 TDDFT（SF-TDDFT）—— 适用于双自由基/键断裂/近简并体系，
可在同一计算中同时得到低自旋与高自旋态。
配方来源：手册 6.1 Chapter 7 **Example 7.23（p.454）**（TMM 双自由基，50-50 泛函）。
$end

$molecule
0 3
{CH3}
$end

$rem
EXCHANGE            gen          ! gen = 走 $xc_functional 自定义
BASIS               6-31G*
SCF_GUESS           core
SCF_CONVERGENCE     10
MAX_SCF_CYCLES      100
SPIN_FLIP           1
CIS_N_ROOTS         6
CIS_CONVERGENCE     10
MAX_CIS_CYCLES      100
MEM_TOTAL           8000
$end

$xc_functional
X    HF      0.50
X    S       0.08
X    B       0.42
C    VWN     0.19
C    LYP     0.81
$end
"""

for name, body in TEMPLATES.items():
    data = body.replace("\r\n", "\n").encode("utf-8")
    assert b"\r" not in data, name
    (OUT / name).write_bytes(data)
    print(f"写出 {name}  ({len(data)} bytes)")

print(f"\n共 {len(TEMPLATES)} 个模板 → {OUT}")
