# Bangumi × Syoboi Calendar ID 映射

把 [Bangumi](https://bgm.tv/) 动画条目 ID 与 [しょぼいカレンダー](https://cal.syoboi.jp/) 的节目标题 ID（TID）对应起来。仓库维护作品身份，放送时间与集数由下游自行读取。

Automatically maintained anime ID mappings between Bangumi and Syoboi Calendar. The JSON dataset can be used without running the collector.

## 映射覆盖率

<!-- mapping-stats:start -->

Bangumi Archive 快照 2026-11-24T21:04:13Z · Syoboi 上游最近编辑 2026-11-26 02:29:37

| 范围 | Syoboi TID | 已关联 | 待关联 | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| 全部历史动画 | 5,817 | 5,467 | 350 | 93.98% |
| 当季 2026 Q4 | 79 | 79 | 0 | 100.00% |
| 2026 | 304 | 300 | 4 | 98.68% |
| 2025 | 290 | 284 | 6 | 97.93% |
| 2024 | 272 | 260 | 12 | 95.59% |

采集了 Syoboi 全分类 **8,109** 个 TID，对照 Bangumi Archive **31,431** 个动画条目，生成 **6,129** 对 ID。
混合电影分类有 **985** 个 TID，其中 **644** 个已关联动画，**63** 个已确认真人电影，**278** 个待核。

<!-- mapping-stats:end -->

覆盖率按具有至少一个映射的 **Syoboi TID 数**计算。动画范围采用 Syoboi 的「动画」「结束／重播动画」「OVA」（Cat 1、10、7）。全部历史动画包含缺少首播年月的 TID。年度按首播年份分组，当季按日本时间的自然季度及 Syoboi 登记的首播或放送年月计算，引进档期和原产地首播可能跨年。Cat 8「电影」混收动画与实拍，单独统计。其他分类也进入全量采集统计。

## 数据文件

| 文件 | 用途 |
| --- | --- |
| [data/mapping.json](data/mapping.json) | 消费端使用的数组。每项含正整数 `bgm_id` 和 `syoboi_tid`，按 TID、Bangumi ID 升序排列。人工确认允许一对多和多对一，按任一方向索引时应保留数组。 |
| [data/unmatched.json](data/unmatched.json) | 未出现在动画映射中的 TID，涵盖待核动画、待核混合电影和已确认的真人电影。最多列出 12 个候选 ID，有证据时附分集匹配数、编号偏移、角色声优一致数或标题相似度。编号偏移为 Bangumi 集号减去 Syoboi 集号。 |
| [data/stats.json](data/stats.json) | 数据来源版本、分类、方法和覆盖率计数。按 Bangumi 快照名及 SHA-256 可识别输入版本。 |
| [data/overrides.json](data/overrides.json) | 人工映射与禁止自动匹配的 ID 对。 |

按 Git 提交固定版本可同时读取同次生成的映射和统计。启用 Releases 后，每次映射变化也会附上同版本的 `mapping.json`。

## 匹配与纠错

人工映射优先。自动规则先按原始标题或规范化标题和首播年月匹配，再检查季数、篇章和分段写法、维基别名、登记的放送年月及作品官网。数字、季数和分段编号分别比较。作品官网在两库均唯一时，同一完整标题可证明跨地区发行或不同放送档期的作品身份。对两库中均唯一的原始标题，可核对相邻月份，缺少首播月份时核对年份。日期靠近月界的规则仍要求 Bangumi 有距月界不超过 10 天的具体日期。

分集交叉验证比较有辨识度的标题和一致的编号偏移，识别一站续编、另一站重编号的情况。分集证据还须得到作品名称、别名或官网的佐证。日期或官网相符时需要至少两条有效分集证据，缺少这类旁证时至少四条。自动关联要求双方候选互为唯一，版本冲突和同一节目涉及多个作品条目时进入复核。原始章节标题仅用于计算。每次更新重新核对当前资料。

当放送和发行日期不同，完整对应的至少三条稀有集名可与同一作品名相互印证。较长的列表也可用两侧至少八成的集名、统一的集号偏移和片名复核。只有 Bangumi 条目确认为电视动画时才把括号中的「TV版」视为标签，重制、总集篇等版本标记仍参与区分。

节目链接可与 Bangumi 登记的官网交叉核对。角色和声优须成对一致，并有作品名称等旁证。仅凭标题相似度的候选进入复核。引进档期、两站拆分粒度不同和候选冲突也可补录人工证据。

引进作品会比较完整译名及两侧登记的季数。片名在两库均唯一时核对发行和播出年月，两侧均明确写出同一季数时可验证更晚的引进档。依靠别名关联时也检查官网域名冲突。

混合电影从 Bangumi 动画和真人条目同时比对。自动方式包括官网、标题和年月同时吻合，或原始片名与上映年月在两类条目中互为唯一。原始片名在两库均唯一时也核对相邻月份，Syoboi 缺少月份时核对上映年份。对两库唯一且上映月份一致的片名，还会比较译名和「剧场版」前缀等写法，并保留续集编号和副标题。同名竞争保留待核，确认的真人电影单列。核心动画覆盖率仍以原 Syoboi 分类的 TID 为分母。

提交纠错时修改 `data/overrides.json`。`mappings` 的每项填写 `bgm_id`、`syoboi_tid`、`syoboi_title`（Syoboi 当前完整标题）和 `evidence`（两侧条目及能证明身份的链接或说明）。改名会触发复核。`blocked_pairs` 的每项只需两个 ID 及 `evidence`。确认某部混合分类电影是动画后，可用相同方式添加映射。

在下一轮采集前，可从 `unmatched.json` 的分集候选入手，核对作品官网和两站的实际覆盖集数。确认后补入 `overrides.json`，用 `python scripts/update.py --offline` 对同一份上游快照重算覆盖率。一个节目覆盖多个条目时，为每个确认的 ID 对留证。

## 更新

需要 Python 3.12 或更高版本，使用标准库。

```sh
python -m unittest discover -s tests -v
python scripts/update.py
python scripts/update.py --offline
```

首次更新从 Bangumi Archive 的 `aux/latest.json` 获取并校验 ZIP，读取动画与三次元条目、正片标题、角色和演职人员关联。Syoboi 使用 `/db?Command=TitleLookup&TID=*` 拉取全量索引，随后按批补取日期、分类、节目链接、别名、分集标题和角色声优。后续仍检查全量索引以识别删除，仅为新增或更新的 TID 补取详情。默认每批最多 100 个 TID，Syoboi 请求串行且间隔至少 1.1 秒。遇到采集期间的上游编辑，会重新核对索引并复用已采集且仍有效的详情。

GitHub Actions 每天北京时间 00:00 触发更新，也可手动触发。默认分支合并人工修正后会刷新。上游响应残缺、校验失败或数据量异常下滑时，工作流报错并保留上一次提交。生成文件有差异才提交。数据许可确认后，仓库变量 `PUBLISH_DATA_RELEASES=true` 可开启自动发布。GitHub 可能延迟整点任务，也可能暂停 60 天无活动的公开仓库定时任务，可在 Actions 页手动恢复。[GitHub 定时工作流说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## 来源与许可

- Bangumi 归档说明、维基与平台字段、导出索引和版权声明。[Bangumi Archive](https://github.com/bangumi/Archive) · [维基模板](https://github.com/bangumi/common/blob/master/wiki_template.yml) · [平台分类](https://github.com/bangumi/common/blob/master/subject_platforms.yml) · [导出索引](https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json) · [Bangumi 版权声明](https://bgm.tv/about/copyright)
- Syoboi TitleLookup、分类、官方链接标注、程序获取许可和访问频率。[TitleLookup](https://docs.cal.syoboi.jp/spec/db.php/) · [分类](https://docs.cal.syoboi.jp/spec/title-cat/) · [编辑规则](https://docs.cal.syoboi.jp/rule/editing-rules/) · [使用说明](https://docs.cal.syoboi.jp/about/) · [频率限制](https://docs.cal.syoboi.jp/spec/rate_limit/)

程序代码采用 [MIT](LICENSE)。采集缓存仅用于计算。上游内容遵循各来源条款，生成数据的再分发许可待维护者确认。
