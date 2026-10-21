---
name: cnipa-patent-agent-distribution
description: 从 CNIPA 官方公布公告系统实证采集指定申请人/专利权人的专利，统计其委托的专利代理机构分布、集中度(CR1/CR3/CR5)与"共同申请人-代理机构"绑定关系，输出 CSV 与分布图。当用户问"某单位/实验室/课题组的专利一般由哪些代理机构代理""查某机构的专利代理所排行""某实验室的专利代理生态"时使用。不适用于单件专利的著录项查询(那种情况直接查公布文本首页更快)。
---

# CNIPA 专利代理机构分布实证采集

## 何时用

用户想知道**某创新主体实际委托了哪些专利代理机构**（而不是"哪家所更权威"这类主观判断）。
典型提问："调查发明人为 XX 实验室的专利，其代理一般都是哪些机构"。

核心价值在于用一手官方数据替代传闻/推测。经验上真正决定委托关系的是**共同申请人**，不是主体本身，所以必须同时采集共同申请人字段。

## 数据源选择（按可靠性排序）

| 源 | 代理机构字段 | 可用性 |
|---|---|---|
| CNIPA 公布公告 `epub.cnipa.gov.cn/Dxb/AdvancedQuery` | ✅ 机构名+代码+代理师 | **首选**，但需浏览器 GUI |
| CN 公布文本 PDF 首页 `patentimages.storage.googleapis.com/{path}` | ✅ `(74)专利代理机构 xxx 11021` | 可靠备选，独立于被封的检索主机 |
| 顾如 patentguru.com | ✅ 含代理排行 | 长亭挑战，需 CDP；游客约 40 次操作/天配额，匿名仅第 1 页 |
| Google Patents `/xhr/query` | ❌ 无代理字段 | 仅用于快速估总量；突发请求会 503 |
| Espacenet | ❌ 无代理字段 | 不适用 |

**结论**：需要代理机构字段时不要在 Google Patents / Espacenet 上浪费时间，直接上 CNIPA。

## CNIPA 三条硬约束（踩过的坑）

1. **瑞数动态防护**：`curl` 只拿到 HTTP 202 + JS 载荷；页内 `fetch` POST 到 `/Dxb/AdvancedQuery` 返回 `len=6` 空响应。**必须走浏览器 GUI 点击**，无法纯 HTTP 复现。
2. **服务端固定 3 条/页**：表单里的 `pageSize` / `sizeSelect` 设成 10 不生效（会拿到 10 条后立即翻页失败）。总量约 N 件 → 需 ⌈N/3⌉ 页。
3. **查询字段**：`catalogInfo.E71_73` = 申请人/专利权人，`catalogInfo.E74` = 专利代理机构。结果记录里隐藏的 `.allinfo` 块含「专利代理机构 + 代理人 + 代理师执业证号」。

## CDP 驱动流程（三段式，必须照做）

**核心约束**：在 CDP `Runtime.evaluate` 里 `click()` 之后 `await sleep()` 会因导航销毁 JS 执行上下文而报 `{"error":"Uncaught"}`。
**因此必须拆成独立调用，中间的等待放在宿主侧（Python `time.sleep`）**：

```
eval(CLICK_SCRIPT)   # 页内：找到含"下页"的 <a> 并 click()，立即返回
time.sleep(10)       # 宿主侧等待，这是关键
eval(PARSE_SCRIPT)   # 页内：解析本轮 3 条，返回 JSON
```

若单次 eval 内串行多个 fetch，会触发 `Runtime.evaluate` 命令超时（CNIPA 每请求约 10 s）→ 改"页内异步 + `window.__P` 轮询"模式。

关闭标签页：CDP Proxy 的 `/close`(含 `?targetId=` 与 JSON body 两种形式)与原生 `/json/close/{id}` 在本机实测**均无效**——标签页会残留，需人工关闭，不要在这上面反复试。

## 页内解析器（已验证可直接复用）

```javascript
window.__EX = function () {
  const items = [...document.querySelectorAll('#result .item')];
  const LBL = ['申请公布号','授权公告号','公布号','公告号','申请公布日','授权公告日','公布日','公告日',
    '申请号','申请日','申请人','专利权人','发明人','地址','分类号','专利代理机构','代理人','摘要'];
  const pat = new RegExp('(' + LBL.join('|') + ')[:：]', 'g');
  return items.map(it => {
    const raw = (it.textContent || '').replace(/\s+/g, ' ').trim();
    const marks = []; let m; pat.lastIndex = 0;
    while ((m = pat.exec(raw)) !== null) marks.push({k: m[1], i: m.index, e: pat.lastIndex});
    const f = {};
    marks.forEach((mk, idx) => {
      const end = idx + 1 < marks.length ? marks[idx + 1].i : Math.min(raw.length, mk.e + 400);
      const v = raw.slice(mk.e, end).trim();
      if (!(mk.k in f)) f[mk.k] = v;
    });
    let firm = f['专利代理机构'] || '';
    let person = '';
    const pm = firm.match(/专利代理师[:：]\s*(.+)$/);
    if (pm) { person = pm[1].trim(); firm = firm.slice(0, pm.index).trim(); }
    const cm = firm.match(/^(.*?)(\d{4,5})$/);      // 机构名尾部 4~5 位代码
    const code = cm ? cm[2] : '';
    if (cm) firm = cm[1].trim();
    const tm = raw.match(/^\s*\[?(发明公布|发明授权|实用新型|外观设计)\]?/);
    return {
      type: tm ? tm[1] : '',
      pn: f['申请公布号'] || f['授权公告号'] || '',
      app_no: f['申请号'] || '', app_date: f['申请日'] || '',
      applicants: (f['申请人'] || f['专利权人'] || '').replace(/全部/g, '').trim(),
      firm, firm_code: code, person,
      ipc: (f['分类号'] || '').split('全部')[0].trim()
    };
  });
};
```

调用（两个独立 eval）：
- 点击：`PARSER + "(()=>{const l=[...document.querySelectorAll('a')].filter(a=>/下页/.test((a.textContent||'').trim()));if(!l.length)return 'no_next';l[0].click();return 'clicked';})()"`
- 解析：`PARSER + "JSON.stringify({n: window.__EX().length, recs: window.__EX()})"`

## 统计与交付

1. 多批次 JSON（正面采集 + 补充切片）**按公布号去重合并**，先打印样本边界自检：总数、公布日范围、申请日范围、年份分布、有代理机构著录的比例。**边界必须写进结论**，否则会被误当成全量。
2. 机构分布表：机构名 / 机构代码 / 件数 / 占比；补 CR1、CR3、CR5、机构总数。
3. **关键分析——共同申请人 × 代理机构交叉表**。实践中规律是"代理机构随共同申请人绑定"（如某高校→某所占 85%+），而不是主体统一指定。这一条往往比排名本身更有决策价值。
4. 交付两份 CSV：`*_agent_distribution.csv`、`*_patents_detail.csv`；配水平条形图 + 共同申请人对应关系图。

## 已知局限（务必向用户声明）

- CNIPA 结果按公布日**降序**返回，前若干页只覆盖最近半年公开的申请 → **近端偏倚**。要消除需采集到全量（页数 = 总量/3）。
- 申请日升序排序（`change_sort('sqr',0)`）实测**未生效**（每页重复返回同 3 条）；如需采早期年份，改为直接翻页到底或换检索式切年份区间，不要指望排序按钮。
- 交互式采集会长时间占用浏览器，建议分批次跑并在间隙释放标签页。

## 单条代理机构查询的快捷路径

只要 1~2 件专利的代理机构时，不必走上述流程：从 `patentimages.storage.googleapis.com` 下载公布文本 PDF，用 pypdf 提取首页文本，正则取 `\(74\)\s*专利代理机构\s*(.+?)\s*(\d{4,5})`。该主机不受 Google 检索限流影响。
