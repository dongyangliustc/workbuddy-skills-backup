#!/bin/bash
# ============================================================================
# Q-Chem FlexNet 许可服务：持久化安装脚本（在许可证主机 login02 上以 root 执行）
# ----------------------------------------------------------------------------
# 解决两件事：
#   ① 重启后自动拉起（systemd 服务）
#   ② 进程意外死亡后自动恢复（Restart=always）
#
# 用法：
#   sudo bash install-qchemlm-service.sh              # 安装 + 启用 + 自检
#   sudo bash install-qchemlm-service.sh uninstall    # 卸载（保留许可证修复）
#
# 前提：必须在许可证主机上执行（本案 login02 / license02，授权 hostid 锁定该机）
# ============================================================================
set -uo pipefail

FN=/work/share/hjbustc/software/qchem61/flexnet
LIC=$FN/etc/qchem_8791.lic
OPT=$FN/etc/qchemlm.opt
LOGDIR=$FN/logs
SVCUSER=hjbustc
PORT=27000
UNIT=/etc/systemd/system/qchemlm.service

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
info() { printf '\033[36m%s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { red "必须以 root 运行（sudo bash $0）"; exit 1; }
[ -f "$LIC" ] || { red "许可证文件不存在: $LIC"; exit 1; }
[ -f "$OPT" ] || warn "选项文件不存在 $OPT（USER_BASED 许可下缺它会报 -39,147）"

# ---------------------------------------------------------------------------
if [ "${1:-install}" = "uninstall" ]; then
  info "== 卸载 =="
  systemctl disable --now qchemlm.service 2>/dev/null
  rm -f "$UNIT"
  systemctl daemon-reload
  ok "已卸载 qchemlm.service（许可证文件的行尾/端口修正予以保留）"
  exit 0
fi

info "== 1/7 备份原许可证 =="
cp -a "$LIC" "$LIC.bak-$(date +%Y%m%d-%H%M%S)"
ok "备份: $(ls -1t "$LIC".bak-* 2>/dev/null | head -1)"

info "== 2/7 修正行尾（CRLF→LF）并显式钉住端口 $PORT =="
# CRLF 是 lmgrd 报 "Invalid License File" 的根因：Linux 下 \r 被并入 token，
# hostid 变成 e8611f37205a\r 而失配。
# 端口必须显式写：不写时 lmgrd 在 27000 被占时会**静默自增**到 27001/27002，
# 而客户端按默认 27000 连接 → 服务看着在跑但客户端全连不上（实测踩过）。
TMP=$(mktemp "$LIC.XXXXXX")
tr -d '\r' < "$LIC" \
  | sed "s|^SERVER login02 e8611f37205a.*|SERVER login02 e8611f37205a $PORT|" > "$TMP"
if ! grep -q "^SERVER login02 e8611f37205a $PORT$" "$TMP" || grep -q $'\r' "$TMP"; then
  red "改写校验失败，已放弃（原文件未改动）"; rm -f "$TMP"; exit 1
fi
chown --reference="$LIC" "$TMP"; chmod --reference="$LIC" "$TMP"
mv "$TMP" "$LIC"
ok "SERVER 行: $(head -1 "$LIC")"

info "== 3/7 准备日志目录 =="
mkdir -p "$LOGDIR" && chown "$SVCUSER:$SVCUSER" "$LOGDIR" && chmod 755 "$LOGDIR"
ok "$LOGDIR（属主 $SVCUSER）"

info "== 4/7 精确清理占用本许可证的旧实例 =="
# 必须清理，否则新 qchemlm 会因"同厂商重复注册"EXITING DUE TO SIGNAL 41，
# lmgrd 随之退出。**但不能全杀 lmgrd** —— login02 是共享许可节点，
# 另一租户的 lmgrd（cmdline 为 ./license.dat）必须保留！
# 判据：① 名为 qchemlm 的进程（另一租户用的是 cstd，不会误伤）
#       ② cmdline 提及 qchem_8791.lic 或 qchem61/flexnet 的 lmgrd
cleanup_ours() {
  # 只看「可执行文件名」（ps 的 args 首 token），避免把仅**提及** qchemlm 字样的
  # 无关进程（tail/grep/本脚本自身）也杀掉。
  #   - 可执行名 == qchemlm      → 我们的厂商守护进程
  #   - 可执行名 == lmgrd 且命令行提及本许可证或本安装目录 → 我们的服务器管理器
  # 另一租户的 lmgrd（cmdline 为 ./license.dat）与 cstd 均不会命中。
  ps -eo pid,args --no-headers | awk '
    { n = split($2, p, "/"); base = p[n] }
    base == "qchemlm" { print $1; next }
    base == "lmgrd" && ($0 ~ /qchem_8791\.lic/ || $0 ~ /qchem61\/flexnet/) { print $1 }
  ' | sort -un
}
PIDS=$(cleanup_ours | tr '\n' ' ')
if [ -n "${PIDS// /}" ]; then
  echo "  需清理的 PIDs: $PIDS"
  # 注意：ps -eo user,pid,args 的字段是 $1=user $2=pid $3..=args，
  # 匹配键必须用 $2（PID），不能用 $1（那是用户名）。
  ps -eo user,pid,args --no-headers \
    | awk -v list="$PIDS" 'BEGIN{n=split(list,a," "); for(i=1;i<=n;i++) want[a[i]]=1}
                           want[$2]{ printf "    %-10s %-7s %s\n", $1, $2, substr($0, index($0,$3), 70) }' \
    | head -8
  kill $PIDS 2>/dev/null; sleep 4
  LEFT=$(cleanup_ours | tr '\n' ' ')
  if [ -n "${LEFT// /}" ]; then
    warn "  仍在运行，强制 kill -9: $LEFT"
    kill -9 $LEFT 2>/dev/null; sleep 3
  fi
else
  echo "  （无占用本许可证的旧实例）"
fi
# 交叉验证：另一租户的 lmgrd 必须仍在（确认我们没有误杀）
OTHER=$(ps -eo args --no-headers | grep -c '[l]mgrd -c ./license.dat')
ok "  其它租户 lmgrd 仍存活数 = $OTHER（应 ≥1；若为 0 说明误杀，需立即排查）"

info "== 5/7 端口占用检查 =="
if netstat -ltn 2>/dev/null | grep -qE ":$PORT[[:space:]]"; then
  warn "  端口 $PORT 仍被占用，服务可能启动失败；请确认占用者："
  netstat -ltnp 2>/dev/null | grep -E ":$PORT[[:space:]]" || true
else
  ok "  端口 $PORT 空闲"
fi

info "== 6/7 安装并启用 systemd 单元 =="
cat > "$UNIT" <<EOF
[Unit]
Description=Q-Chem FlexNet license server (lmgrd + qchemlm)
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
User=$SVCUSER
Group=$SVCUSER
WorkingDirectory=$FN
Environment=PATH=$FN/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=$FN/bin/lmgrd -c $LIC -l +$LOGDIR/qchemlm.log
Restart=always
RestartSec=5
LimitNOFILE=65536
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$UNIT"
systemctl daemon-reload
systemctl enable --now qchemlm.service
ok "已写入 $UNIT 并 enable --now"

info "== 7/7 自检（最多等 30s 等 qchemlm UP）=="
UP=no
for i in $(seq 1 15); do
  sleep 2
  if "$FN/bin/lmutil" lmstat -c "$LIC" -a 2>&1 | grep -q "qchemlm: UP"; then UP=yes; break; fi
done
echo "  systemd : $(systemctl is-active qchemlm.service 2>/dev/null)"
echo "  进程    : $(ps -eo user,pid,comm --no-headers | grep -E 'lmgrd|qchemlm' | grep -v grep | tr '\n' '|')"
echo "  监听    : $(netstat -ltn 2>/dev/null | grep -E ":$PORT[[:space:]]" | tr -s ' ' | cut -d' ' -f4 | tr '\n' ' ')"
"$FN/bin/lmutil" lmstat -c "$LIC" -a 2>&1 | sed -n '1,10p' | sed 's/^/    /'
echo
if [ "$UP" = yes ]; then
  ok "✔ 许可服务已作为系统服务运行，重启后会自动拉起"
  echo "    systemctl status qchemlm        # 状态"
  echo "    systemctl restart qchemlm       # 重启（改许可证/选项文件后）"
  echo "    journalctl -u qchemlm -n 50     # 日志"
  echo "    $FN/bin/lmutil lmreread -c $LIC  # 仅重读许可证，无需重启"
else
  red "✘ qchemlm 未就绪，请检查："
  echo "    systemctl status qchemlm -l"
  echo "    journalctl -u qchemlm -n 80"
  echo "    tail -30 $LOGDIR/qchemlm.log"
fi
