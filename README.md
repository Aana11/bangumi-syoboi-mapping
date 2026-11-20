# Bangumi × Syoboi Calendar ID 映射

把 [Bangumi](https://bgm.tv/) 动画条目 ID 与 [しょぼいカレンダー](https://cal.syoboi.jp/) 的节目标题 ID（TID）对应起来。仓库维护作品身份，放送时间与集数由下游自行读取。

Automatically maintained anime ID mappings between Bangumi and Syoboi Calendar. The JSON dataset can be used without running the collector.

## 映射覆盖率

<!-- mapping-stats:start -->

Bangumi Archive 快照 2026-11-17T21:04:20Z · Syoboi 上游最近编辑 2026-11-20 23:35:38

| 范围 | Syoboi TID | 已关联 | 待关联 | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| 全部历史动画 | 5,817 | 4,456 | 1,361 | 76.60% |
| 当季 2026 Q4 | 79 | 57 | 22 | 72.15% |
| 2026 | 304 | 228 | 76 | 75.00% |
| 2025 | 290 | 231 | 59 | 79.66% |
| 2024 | 272 | 202 | 70 | 74.26% |

采集了 Syoboi 全分类 **8,108** 个 TID，对照 Bangumi Archive **31,312** 个动画条目，生成 **4,456** 对 ID。
混合电影分类有 **984** 个 TID，其中 **0** 个已人工核实关联。

<!-- mapping-stats:end -->

覆盖率按具有至少一个映射的 **Syoboi TID 数**计算。动画范围采用 Syoboi 的「动画」「结束／重播动画」「OVA」（Cat 1、10、7）。全部历史动画包含缺少首播年月的 TID。年度按首播年份分组，当季按日本时间的自然季度及 Syoboi 首播年月计算。Cat 8「电影」混收动画与实拍，单独列出，核实后可通过人工映射纳入数据。其他分类也进入全量采集统计。

## 数据文件

| 文件 | 用途 |
| --- | --- |
| [data/mapping.json](data/mapping.json) | 消费端使用的数组。每项含正整数 `bgm_id` 和 `syoboi_tid`，按 TID、Bangumi ID 升序排列。人工确认允许一对多和多对一，按任一方向索引时应保留数组。 |
| [data/unmatched.json](data/unmatched.json) | 待复核的动画及混合电影 TID，包含首播年月、原因及最多 12 个候选 ID。 |
| [data/stats.json](data/stats.json) | 数据来源版本、分类、方法和覆盖率计数。按 Bangumi 快照名及 SHA-256 可识别输入版本。 |
| [data/overrides.json](data/overrides.json) | 人工映射与禁止自动匹配的 ID 对。 |

按 Git 提交固定版本可同时读取同次生成的映射和统计。启用 Releases 后，每次映射变化也会附上同版本的 `mapping.json`。

## 匹配与纠错

人工映射优先。自动规则先检查原始标题与首播年月，再检查规范化标题与首播年月。每一步都要求双方在同一匹配键下唯一。规范化处理 Unicode、大小写、空格和少量排版符号，保留数字、标题词和能区别作品版本的标点。日期缺失、年月不同、碰撞、拆分季及混合电影交给人工复核。每次以当前资料重验自动结果，以应对上游更正或复用 TID。

提交纠错时修改 `data/overrides.json`。`mappings` 的每项填写 `bgm_id`、`syoboi_tid`、`syoboi_title`（Syoboi 当前完整标题）和 `evidence`（两侧条目及能证明身份的链接或说明）。改名会触发复核。`blocked_pairs` 的每项只需两个 ID 及 `evidence`。确认某部混合分类电影是动画后，可用相同方式添加映射。

## 更新

需要 Python 3.12 或更高版本，使用标准库。

```sh
python -m unittest discover -s tests -v
python scripts/update.py
python scripts/update.py --offline
```

首次更新读取 Bangumi Archive 的 `aux/latest.json`，下载并校验 ZIP，从 `subject.jsonlines` 提取动画条目。Syoboi 使用 `/db?Command=TitleLookup&TID=*` 拉取全量索引，随后按批补取首播年月和分类。后续仍检查全量索引以识别删除，仅为新增或更新的 TID 补取详情。默认每批最多 100 个 TID，Syoboi 请求串行且间隔至少 1.1 秒。遇到采集期间的上游编辑，会重新核对索引并复用已采集且仍有效的详情。

GitHub Actions 每周三北京时间 08:17 更新，也可手动触发。默认分支合并人工修正后会刷新。上游响应残缺、校验失败或数据量异常下滑时，工作流报错并保留上一次提交。生成文件有差异才提交。数据许可确认后，仓库变量 `PUBLISH_DATA_RELEASES=true` 可开启自动发布。GitHub 可能暂停 60 天无活动的公开仓库定时任务，可在 Actions 页手动恢复。[GitHub 定时工作流说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## 来源与许可

- Bangumi 归档说明、导出索引和版权声明。[Bangumi Archive](https://github.com/bangumi/Archive) · [导出索引](https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json) · [Bangumi 版权声明](https://bgm.tv/about/copyright)
- Syoboi TitleLookup、分类、程序获取许可和访问频率。[TitleLookup](https://docs.cal.syoboi.jp/spec/db.php/) · [分类](https://docs.cal.syoboi.jp/spec/title-cat/) · [使用说明](https://docs.cal.syoboi.jp/about/) · [频率限制](https://docs.cal.syoboi.jp/spec/rate_limit/)

程序代码采用 [MIT](LICENSE)。采集缓存仅用于计算。上游内容遵循各来源条款，生成数据的再分发许可待维护者确认。
