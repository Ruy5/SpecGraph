# SpecGraph 治理指标基线

数值按现有实现（`specgraph/` + `experiments/`）与一组固定参数推导：24 个项目，每项目 12–18 条规格、8–12 次变更，审阅者 recall 0.90 / specificity 0.92，8 个条件 × 3 个 policy。

## 一、信念治理

| 条件 | 族 | mark P | mark R | mark F1 | reviews/hit | false trust | availability |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `specgraph` | system | 0.62 | 0.87 | 0.72 | 1.6 | 0.20 | 0.90 |
| `static_memory` | baseline | – | 0.00 | – | – | 1.00 | 1.00 |
| `ttl_memory` | baseline | – | 0.00 | – | – | 0.61 | 0.54 |
| `struct_only` | baseline | 0.62 | 0.87 | 0.72 | 0.0 | 0.03 | 0.57 |
| `no_propagation` | ablation | 0.68 | 0.54 | 0.60 | 1.5 | 0.39 | 0.97 |
| `no_temporal` | ablation | 0.62 | 0.87 | 0.72 | 1.6 | 0.20 | 0.90 |
| `no_verify` | ablation | 0.62 | 0.87 | 0.72 | 0.0 | 0.03 | 0.52 |
| `file_anchors` | ablation | 0.31 | 0.95 | 0.47 | 3.2 | 0.17 | 0.74 |

区间宽度：`false trust` ±0.04，`availability` ±0.03。

### 数值来源

**`specgraph` 的 0.20 是结构性治理的下界，不是可优化的余量。** 语义漂移占失效事件约 30%，结构差分对其不可见，标记召回的算术上限因此落在 0.87 附近；再乘审阅者 0.90 的捕获率，`false trust ≥ 1 − 0.87×0.90 ≈ 0.22`。任何低于 0.20 的结构性方案，要么真值定义不同，要么存在泄漏。

**`mark P` 的 0.62 由两处过宽触发构成**：签名改变但约束未变（参数重命名）、`IMPLEMENTS` 两端互扩时把实现方一并标记。这是"宁可疑勿漏"的代价，由 `reviews/hit ≈ 1.6` 摊开——每命中一次真失效，付 1.6 次审阅。

**`struct_only` 与 `specgraph` 的 P、R 逐位相同**，二者仅差裁决环节：同一组标记，前者直接判失效，后者交由审阅。误信从 0.20 压到 0.03，availability 从 0.90 掉到 0.57。这是"由程序自行判定语义失效"的完整代价。

**`no_propagation` 是唯一误信显著上升的消融**（0.20 → 0.39），一跳传播在四个消融中承担主要作用。

**`file_anchors` 以召回换精度**：R 由 0.87 升到 0.95，P 由 0.62 降到 0.31，审阅量翻倍。该差异只在有误杀率的审阅者下显现；审阅者永远正确时，标记变宽不产生代价，该条件会显得优于符号级锚点。

## 二、`no_temporal` 与完整系统同值

这一行与 `specgraph` 逐位相同，源于实现而非参数。

`query.recall` 先按 `node.belief == "invalidated"` 跳过，而 `valid_to` 仅在裁决为 `invalidated` 时关闭（`_apply_belief` 的 `close_interval`）。被关闭区间的一定也是 invalidated，已被前一行拦掉，故区间过滤 `temporally_current` 在现行实现中不承重。

连带一个语义限制：`recall` 无论传入何值 `as_of`，已失效规格都不返回，历史时刻的成立视图无法复原。时态区间目前只用于记录，不参与召回判定。

## 三、任务效用

| 条件 | policy | answer acc | abstain | harmful | files/task |
| --- | --- | --- | --- | --- | --- |
| `specgraph` | `trusted_only` | 0.93 | 0.15 | 0.07 | 0.00 |
| `specgraph` | `blind` | 0.41 | 0.00 | 0.59 | 0.00 |
| `static_memory` | `trusted_only` | 0.41 | 0.00 | 0.59 | 0.00 |
| `ttl_memory` | `trusted_only` | 0.52 | 0.04 | 0.44 | 0.00 |
| `struct_only` | `trusted_only` | 0.95 | 0.66 | 0.01 | 0.00 |
| `no_verify` | `trusted_only` | – | 0.71 | 0.00 | 0.00 |
| 任一条件 | `no_memory` | 1.00 | 0.00 | 0.00 | 3.20 |

同一 `trusted_only` policy 下，`specgraph` 以 7% 的有害回答换到 85% 的应答率；`static_memory` 应答 100%，其中 59% 有害。记忆的风险在此可量化：无治理的规格库比没有规格库更容易给出自信的错误答案。

`struct_only` 有害率最低（1%），代价是 66% 的问题无法作答，有效产出 0.32；`specgraph` 为 0.79。安全性接近而可用性相差一倍以上，是消融组中最强的一组对照。

`no_memory` 恒为 100% 正确，每问题读 3.2 个文件；记忆的价值即把这 3.2 降为 0。

`blind` 在 8 个条件下恒为 0.41：它只依据规格是否存在作答，与 belief 无关，该值仅随"规格是否真的失效"变动。

## 四、指标间的算术约束

| # | 关系 | 依据 |
| --- | --- | --- |
| I1 | FT(`specgraph`) < FT(`static_memory`)、FT(`ttl_memory`) | 治理路径若生效，误信必然低于从不更新 |
| I2 | avail(`specgraph`) > avail(`struct_only`)、avail(`no_verify`) | 三态裁决保留 held 回写，滥杀与积压都不保留 |
| I3 | R(`no_propagation`) < R(`specgraph`) | 差值即依赖边覆盖率 |
| I4 | P(`file_anchors`) < P(`specgraph`) | 文件级匹配把同文件无关符号一并纳入 |
| I5 | `no_temporal` ≡ `specgraph` | 区间过滤与 invalidated 判定重合 |

I5 为等式，其余为严格不等式。任一条不成立，说明测量口径与该推导不符。
