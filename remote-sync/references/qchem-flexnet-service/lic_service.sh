#!/bin/bash
# ============================================================================
# Q-Chem FlexNet 许可服务管理（无 root 的降级方案）
#   落盘位置（HPCCube）：/work/home/dongyang/.qchemlic/lic_service.sh
#   用法：bash lic_service.sh {start|stop|status|restart}
# ----------------------------------------------------------------------------
# 何时用它：
#   - 你只有普通账号（无 root），无法安装 systemd 服务；
#   - 作为 systemd 服务部署前的临时手段；
#   - 现场排障时手动拉起。
#
# 局限（必须知情）：进程随节点重启消失，也不被 systemd 守护。
#   长期正解见同目录 install-qchemlm-service.sh（root）。
#
# 稳健性要点（全部来自真机踩坑）：
#   1. 非交互 SSH shell 的 PATH 不可靠（实测缺 ss）→ 脚本内显式设 PATH。
#   2. `export PATH=<dir>` 漏写 `:$PATH` 会覆盖整个 PATH，后续命令全 not found。
#   3. 同一主机不得有两个同厂商 vendor daemon：残留旧实例会让新 qchemlm
#      `EXITING DUE TO SIGNAL 41` 并使 lmgrd 一并退出 → 必须先清零。
#   4. 停止→启动之间存在注册释放竞态 → 单次启动可能失败，故内置 3 次重试。
#   5. 判据不能只看进程存活：必须同时满足
#      「日志出现 Listening port: <PORT>」+「lmgrd 进程」+「qchemlm 进程」。
# ============================================================================
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
U=dongyang
FN=/work/share/hjbustc/software/qchem61/flexnet
RD=/work/home/$U/.qchemlic
LIC=$RD/qchem_8791.lic
LOG=$FN/qchemlm.log
LP=27000
mkdir -p $RD

ensure_lic() {
  # 原始 lic 为 CRLF；Linux 下 \r 被并入 token 会导致 "Invalid License File"。
  # 同时显式钉住端口：不写端口时 lmgrd 会在 27000 被占时**静默自增**到 27001/27002，
  # 而客户端按默认 27000 连接 → 服务看着在跑但客户端全连不上。
  tr -d '\r' < $FN/etc/qchem_8791.lic \
    | sed "s|^SERVER login02 e8611f37205a.*|SERVER login02 e8611f37205a $LP|" > $LIC
}

alive_count() {
  ps -eo user,pid,comm --no-headers | grep -E "^$U .* (lmgrd|qchemlm)$" | wc -l
}

do_stop() {
  pkill -u $U -x qchemlm 2>/dev/null
  pkill -u $U -x lmgrd   2>/dev/null
  for i in $(seq 1 20); do [ "$(alive_count)" -eq 0 ] && break; sleep 1; done
  if [ "$(alive_count)" -ne 0 ]; then
    pkill -9 -u $U -x qchemlm 2>/dev/null
    pkill -9 -u $U -x lmgrd   2>/dev/null
    sleep 3
  fi
  echo "  stop: 剩余实例 = $(alive_count)"
}

running_ok() {
  grep -qa "Listening port: $LP" $LOG || return 1
  ps -eo user,pid,comm --no-headers | grep -qE "^$U .* lmgrd$" || return 1
  ps -eo user,pid,comm --no-headers | grep -qE "^$U .* qchemlm$" || return 1
  return 0
}

do_start() {
  ensure_lic
  echo "  lic 首行: $(sed -n '1p' $LIC)"
  for attempt in 1 2 3; do
    echo "  --- 第 $attempt 次尝试 ---"
    export PATH=$FN/bin:$PATH
    NLINES=$(wc -l < $LOG)
    cd $RD
    setsid nohup $FN/bin/lmgrd -c $LIC -l +$LOG </dev/null >lmgrd.out 2>&1 &
    for i in $(seq 1 25); do sleep 1; running_ok && break; done
    tail -n +$((NLINES+1)) $LOG \
      | grep -aE "Listening port|Started qchemlm|EXITING|already running|Invalid|cannot" \
      | tail -6 | sed 's/^/    /'
    if running_ok; then echo "  ✔ 启动成功"; return 0; fi
    echo "  ✗ 未就绪，清场后重试"
    do_stop
    sleep 5
  done
  echo "  ✗ 三次尝试均失败，请查看 $RD/lmgrd.out 与 $LOG"
  return 1
}

do_status() {
  echo "  进程:"; ps -eo user,pid,comm --no-headers | grep -E "$U.*(lmgrd|qchemlm)" | sed 's/^/    /'
  echo "  监听:"; netstat -ltn 2>/dev/null | grep -E ":(27000|27002)" | sed 's/^/    /' || echo "    (无)"
  echo "  lmstat:"; $FN/bin/lmutil lmstat -c $LIC -a 2>&1 | sed -n '1,12p' | sed 's/^/    /'
}

case "$1" in
  start)   do_start ;;
  stop)    do_stop ;;
  status)  do_status ;;
  restart) do_stop; do_start ;;
  *) echo "用法: $0 {start|stop|status|restart}"; exit 2 ;;
esac
