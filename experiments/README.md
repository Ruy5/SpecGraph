# 评估框架

给 SpecGraph 补的量化评估。两件事：**信念治理是否管用**，以及**这份记忆对回答问题的价值**。整套东西不连 Neo4j、不调模型，跑的是真实的 `scan` / `consume` / `verify` 代码路径，只把存储换成内存字典。

## 快速开始

```bash
python -m experiments all \
  --scenario experiments/scenarios/demo.json \
  --out results --ci
```

产出：

- `results/report.md`：两张 Markdown 表，可直接改进论文
- `results/<scenario>-steps.csv`：每一步每个条件的明细，用于画曲线
- `results/<scenario>-tasks.csv`：每个任务的判定明细

`--variants specgraph,static_memory` 可只跑部分条件。`--seed` 控制 bootstrap 抽样，模拟本身是确定性的，同一场景重跑结果完全相同。

## 实验一：信念治理

对每个变更步，比较系统「此刻认为可信的规格」与手工标注的「此刻真的失效的规格」。

| 指标 | 定义 | 方向 |
| --- | --- | --- |
| mark precision | 本步新标可疑中真失效的比例 | 高好 |
| mark recall | 本步真失效中被标出的比例 | 高好 |
| reviews per hit | 每找到一个真失效，要人看几条 | 低好 |
| false trust | 真失效却仍被 `consume` 判为 `trusted` 的条数 | 低好 |
| false trust rate | false trust ÷ 当前失效总数 | 低好 |
| availability | 仍成立的规格中被判为 `trusted` 的比例 | 高好 |
| pending / step | 平均每步待查规模（人工成本） | 低好 |

两个必须同时报的原因：**只报 false trust，一个把所有规格都判失效的系统会拿满分**；只报 availability，一个什么都不治理的系统也会拿满分。这组指标是对偶的，缺一个就能被刷。

`mark precision` 低不代表系统差。`scan` 的设计取向是「宁可疑勿漏」，precision 天然偏低，代价体现在 `reviews per hit`。论文里要把这条写清楚，否则审稿人会误读成误报率高。

## 变体

每个变体只改一个具名开关，其余完全相同，所以差异可以归因。

| 变体 | 族 | 关掉的开关 | 想证明什么 |
| --- | --- | --- | --- |
| `specgraph` | system | 无 | 完整闭环 |
| `static_memory` | baseline | 不扫描、不裁决 | 静态 Markdown 记忆的腐化速度 |
| `ttl_memory` | baseline | 不扫描，固定寿命过期 | 只靠时间过期，既漏检又误杀 |
| `struct_only` | baseline | 三态改成直接判失效 | 程序自行判语义失效的误杀代价 |
| `no_propagation` | ablation | 不沿 `IMPLEMENTS` / `DEPENDS_ON` 扩一跳 | 一跳传播贡献多少召回 |
| `no_temporal` | ablation | 召回不看有效区间 | 时态过滤挡住多少过期知识 |
| `no_verify` | ablation | 无人裁决 | 只标不判，待查集合会怎样 |
| `file_anchors` | ablation | 锚点退化到整文件 | 符号级锚点省下多少误报 |

`struct_only` 既是 baseline 也是消融：它对应「让程序自己判断规格是否失效」这一常见做法，正是本文反对的。

## 实验二：任务效用

治理指标衡量记账，效用指标衡量读者真正关心的事：这份记忆让 agent 答对了多少架构问题，代价是什么。

三个可插拔的答题策略，**都不知道标准答案**，判定由 runner 统一做：

| policy | 行为 | 文件读取 |
| --- | --- | --- |
| `no_memory` | 每次从源码重新推导 | 全额 |
| `trusted_only` | 目标规格是 `trusted` 才作答，否则弃权 | 0 |
| `blind` | 只要有这条规格就作答，不看信念 | 0 |

| 指标 | 定义 |
| --- | --- |
| answer accuracy | 作答中答对的比例 |
| abstain | 拒答比例 |
| harmful | 作答且答错的比例，**这是静默腐化的直接度量** |
| files / task | 平均读取文件数 |

`trusted_only` 与 `blind` 的差别就是治理的全部意义：把「答错」换成「说我不能确定」。`no_memory` 是准确率上限，同时给出记忆本该省下的读取成本。

## 场景格式

一个场景是一个 JSON：

- `specs`：规格声明，顺序须保证被依赖者先出现（建图时校验目标存在）
- `steps`：变更序列，每步给出
  - `changes[].before` / `after`：该文件**完整**的符号表，不是增量。校验器会检查上一步的 `after` 与本步的 `before` 一致
  - `body_changed`：符号不变但正文变了，用于触发 non-structural 分支
  - `newly_stale`：本步之后真的失效的规格，手工标注，单调不复活
  - `tasks`：可选的问答，`answer_valid` 是规格里的说法，`answer_stale` 是代码变化后的真实行为

标注原则：`newly_stale` 只写**语义上真的不再成立**的规格，不写「结构变了但意思没变」的。后者正是 `scan` 会标、`reviewer` 会放回的样本，是 precision 的分母。

## 预期模式

以下是结构上的必然，跑出来不符合说明 harness 或场景有问题：

- `static_memory` 的 false trust 随失效规格数单调上升，availability 恒为 1
- `struct_only` 的 false trust 很低，但 availability 明显低于 `specgraph`，因为误杀的有效规格再也回不来
- `no_verify` 的 false trust 与 `specgraph` 接近，availability 随待查集合累积持续下降
- `no_propagation` 在主变更步只标 anchor 命中的那一条，漏掉依赖方，recall 明显下降
- `file_anchors` 在同一文件有多条规格时误标变多，`reviews per hit` 上升
- `specgraph` 的 false trust 不为 0：`notify-async` 这一步语义变了但符号没变，结构差分按设计看不见。这不是 bug，是文章要承认的边界

## 论文里怎么写

方法段可以直接用这个框架的描述：场景由人工标注真值，系统跑真实代码路径，条件之间只差一个开关。

结果段建议先报 `specgraph` 的 false trust 与 availability，再报 baseline 的对照，最后用 `no_propagation` / `no_temporal` / `file_anchors` 说明各组件的边际贡献。`false trust` 与 `availability` 要成对出现，并在正文里解释为什么单看一个会被平凡策略刷满。

## 必须写进 limitation 的

1. **reviewer 是 oracle**：`verify` 的裁决用真值模拟，衡量的是机制上限，不是真实人/模型的判断质量。要评估真实 reviewer，需要接一个标注流程或一个模型，并报告其错误率。
2. **单一场景、8 条规格**：demo 只用于打通流程，投稿前需要多个项目、几十到上百条规格，规模统计才有说服力。
3. **真值手工标注**：`newly_stale` 由作者判定，存在主观性；多人标注 + Cohen's kappa 会更稳。
4. **只覆盖结构差分能看见的变更**：文件级新增、重命名、跨文件移动未建模。
5. **效用实验的答案是事实型**：真实 agent 任务包含多跳推理和部分信息，这里只测单跳事实，属于上界估计。

## 加一个场景

复制 `scenarios/demo.json` 改内容即可，`load_scenario` 会做引用完整性、单调性、符号表连续性校验，报错信息带位置。多个场景用 `--scenario` 重复传入，报告会分节输出。
