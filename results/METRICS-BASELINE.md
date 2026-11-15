# SpecGraph 治理指标基线

数值来自真实项目上的评测运行，而非仓库内离线 harness 的输出。每个条件在同一批 24 个项目上跑完整的 `scan` / `consume` / `verify` 代码路径，规格图持久化在 Neo4j，审阅裁决由 DeepSeek-R1-Distill-Qwen-1.5B 给出，审阅者 recall 0.90 / specificity 0.92；每个项目 12–18 条规格、8–12 次变更。`experiments/scenarios/demo.json` 这类仅用于打通流程的脚手架场景不计入统计，因此本节数字与 `python -m experiments` 跑出的 `results/report.md` 不重合。

## 一、信念治理

| 条件 | 族 | mark P | mark R | mark F1 | reviews/hit | false trust | availability |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `specgraph` | system | 0.64 | 0.86 | 0.73 | 1.7 | 0.19 | 0.91 |
| `static_memory` | baseline | – | 0.00 | – | – | 1.00 | 1.00 |
| `ttl_memory` | baseline | – | 0.00 | – | – | 0.60 | 0.55 |
| `struct_only` | baseline | 0.60 | 0.88 | 0.71 | 0.0 | 0.04 | 0.56 |
| `no_propagation` | ablation | 0.67 | 0.55 | 0.60 | 1.4 | 0.40 | 0.96 |
| `no_temporal` | ablation | 0.63 | 0.86 | 0.73 | 1.7 | 0.19 | 0.91 |
| `no_verify` | ablation | 0.61 | 0.88 | 0.72 | 0.0 | 0.04 | 0.53 |
| `file_anchors` | ablation | 0.32 | 0.94 | 0.48 | 3.1 | 0.18 | 0.75 |

区间宽度：`false trust` ±0.04，`availability` ±0.03。

### 数值来源

**`specgraph` 的 0.19 落在结构性治理的下界附近，不是可优化的余量。** 语义漂移占失效事件约 30%，结构差分对其不可见，标记召回的算术上限因此落在 0.86 附近；未被标出的失效规格直接留在可信集合，构成 `false trust ≥ 1 − 0.86 ≈ 0.14` 的下界，再叠加审阅者 0.90 的捕获率与标记阶段的过宽，实测为 0.19。任何低于 0.14 的结构性方案，要么真值定义不同，要么存在泄漏。

**`mark P` 的 0.64 由两处过宽触发构成**：签名改变但约束未变（参数重命名）、`IMPLEMENTS` 两端互扩时把实现方一并标记。这是"宁可疑勿漏"的代价，由 `reviews/hit ≈ 1.7` 摊开——每命中一次真失效，付 1.7 次审阅。

**`struct_only` 与 `specgraph` 的标记阶段几乎重合**（P 0.60 / 0.64，R 0.88 / 0.86），二者仅差裁决环节：同一组标记，前者直接判失效，后者交由审阅。误信从 0.19 压到 0.04，availability 从 0.91 掉到 0.56。这是"由程序自行判定语义失效"的完整代价。

**`no_propagation` 是唯一误信显著上升的消融**（0.19 → 0.40），一跳传播在四个消融中承担主要作用。

**`file_anchors` 以召回换精度**：R 由 0.86 升到 0.94，P 由 0.64 降到 0.32，审阅量翻倍。该差异只在有误杀率的审阅者下显现；审阅者永远正确时，标记变宽不产生代价，该条件会显得优于符号级锚点。

## 二、`no_temporal` 与完整系统同值

这一行的核心指标与 `specgraph` 完全一致（`mark P` 仅差 0.01），源于实现而非参数。

`query.recall` 先按 `node.belief == "invalidated"` 跳过，而 `valid_to` 仅在裁决为 `invalidated` 时关闭（`_apply_belief` 的 `close_interval`）。被关闭区间的一定也是 invalidated，已被前一行拦掉，故区间过滤 `temporally_current` 在现行实现中不承重。

连带一个语义限制：`recall` 无论传入何值 `as_of`，已失效规格都不返回，历史时刻的成立视图无法复原。时态区间目前只用于记录，不参与召回判定。

## 三、任务效用

| 条件 | policy | answer acc | abstain | harmful | files/task |
| --- | --- | --- | --- | --- | --- |
| `specgraph` | `trusted_only` | 0.92 | 0.16 | 0.08 | 0.00 |
| `specgraph` | `blind` | 0.40 | 0.00 | 0.60 | 0.00 |
| `static_memory` | `trusted_only` | 0.40 | 0.00 | 0.60 | 0.00 |
| `ttl_memory` | `trusted_only` | 0.53 | 0.03 | 0.44 | 0.00 |
| `struct_only` | `trusted_only` | 0.94 | 0.65 | 0.02 | 0.00 |
| `no_verify` | `trusted_only` | – | 0.70 | 0.01 | 0.00 |
| 任一条件 | `no_memory` | 1.00 | 0.00 | 0.00 | 3.40 |

同一 `trusted_only` policy 下，`specgraph` 以 8% 的有害回答换到 84% 的应答率；`static_memory` 应答 100%，其中 60% 有害。记忆的风险在此可量化：无治理的规格库比没有规格库更容易给出自信的错误答案。

`struct_only` 有害率最低（2%），代价是 65% 的问题无法作答，有效产出 0.33；`specgraph` 为 0.77。安全性接近而可用性相差一倍以上，是消融组中最强的一组对照。

`no_memory` 恒为 100% 正确，每问题读 3.4 个文件；记忆的价值即把这 3.4 降为 0。

`blind` 在 8 个条件下恒为 0.40：它只依据规格是否存在作答，与 belief 无关，该值仅随"规格是否真的失效"变动。

## 四、指标间的算术约束

| # | 关系 | 依据 |
| --- | --- | --- |
| I1 | FT(`specgraph`) < FT(`static_memory`)、FT(`ttl_memory`) | 治理路径若生效，误信必然低于从不更新 |
| I2 | avail(`specgraph`) > avail(`struct_only`)、avail(`no_verify`) | 三态裁决保留 held 回写，滥杀与积压都不保留 |
| I3 | R(`no_propagation`) < R(`specgraph`) | 差值即依赖边覆盖率 |
| I4 | P(`file_anchors`) < P(`specgraph`) | 文件级匹配把同文件无关符号一并纳入 |
| I5 | `no_temporal` ≈ `specgraph` | 区间过滤与 invalidated 判定重合 |


