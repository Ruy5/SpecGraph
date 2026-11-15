# SpecGraph

SpecGraph 给代码智能体维护一份项目级工程记忆。架构规格放在 Neo4j 的时态图里，会话结束后仍然可以召回；代码结构变化时，受影响的规格被标成可疑，等到真正使用时再裁决它是否还成立。仍然正确的知识留在图中，可能过期的知识变成必须核验的显式状态。

## 两层机制

代码智能体有两个叠在一起的问题。

跨会话架构失忆。模块边界、依赖、架构决策和接口约定如果只活在一次对话里，下次任务就得重读材料并重新推断。SpecGraph 把这些长效工程知识建成带有效区间的图：节点是规格，边是它们之间的关联。记忆属于项目，不跟随某一次会话、某一个模型或某一次运行消失。召回按查询时刻过滤，返回当时仍然有效的规格。

静默记忆腐化。规格存下来之后，代码还在变，静态内容会逐渐失效。失效若仍被当作事实使用，错误本身没有痕迹。SpecGraph 把有效性治理做成一条闭环：结构差分发现代码变了，程序把受扰动的规格标为可疑，不直接判它失效；调用方在消费这条知识时做出「仍成立 / 已失效 / 无法判断」的裁决，并写回图中。隐性过期由此变成可查询、可沿事件追踪、可按待查集合计数的状态。

标记由 `scan` 自动完成。语义是否仍然成立由消费时的 `verify` 写入，结构差分不代替这个判断。

## 贡献

1. 记忆不再绑在会话上。项目级规格图持久留在 Neo4j 中，换会话之后仍按有效区间召回。
2. 治理对象是工程规格，包括目标与需求、架构设计、模块与子模块设计、任务规格，而不是通用自然语言事实或从对话里抽出的浅层实体。架构决策和模块约束写在这些规格及其关系里。
3. 更新闭环由结构变更触发，并由消费时的三态核验完成。图不会在缺少裁决时被直接改写成失效，也不会在代码变化后继续把旧规格当作可信知识交出去。

## 与现有图记忆的差别

| 系统 | 它治理的对象 | SpecGraph 在这里的做法 |
| --- | --- | --- |
| WorldDB | 通用自然语言事实，由用户更新 | 对象是工程规格；结构变更自动把相关规格标为待核验 |
| Graphiti | 从对话交互抽取实体 | 规格和关系由项目显式写入，留在 Neo4j 中，不依附某一次会话 |
| Exocore | 只读的静态代码结构索引 | 在结构指纹之外保存规格本身，并维护有效区间、信念和校验事件 |

## 图里有什么

一条规格是一个 `Spec` 节点。类型是 `goal-and-requirements`、`architecture-design`、`module-design`、`submodule-design`、`task-spec`。

节点保存标题、陈述与边界、编写状态、代码锚点，以及两套时效：

- `valid_from` / `valid_to`：这段记忆在哪段时间里算数。区间左闭右开；没有 `valid_to` 表示向后开放。
- `belief`：`held`、`suspicious`、`invalidated`、`unverifiable`。

关系是 `PARENT`、`DEPENDS_ON`、`REFERENCES`、`IMPLEMENTS`。`status` 只表示编写进度：`draft`、`active`、`stale`、`done`、`deprecated`。代码变更改的是 `belief`。

`baseline`、`perturb`、`verify` 记成 `Event` 节点，并连回对应规格。项目用 `SPECGRAPH_PROJECT` 区分；不设置时使用仓库路径。

## 变更只标可疑

`scan` 为 Python、JavaScript 和 TypeScript 做结构指纹。Python 使用语法树；脚本语言抽取函数、类、接口和类型声明。比较对象是上一轮快照，或某次 git 修订。差分识别符号的新增、删除和签名变化。

指纹命中 `anchors` 之后，沿关系再扩一跳：`IMPLEMENTS` 的两端，以及 `DEPENDS_ON` 中依赖被改动规格的那一端。这些节点写成 `suspicious`，并记下原因。有效区间保持不动。已经 `invalidated` 的节点跳过。正文变化而符号未变时，记成非结构变更，不标记规格。

`.specgraph/structure.json` 只保存上一轮代码指纹。它是差分基线，不是规格记忆。

## 使用时才裁决

`recall` 按查询时刻取出仍在有效区间内的规格。`held` 进入可信集合，`suspicious` 和 `unverifiable` 进入待查集合。已失效、区间之外，以及状态为 `done` 的 `task-spec` 不返回。

`consume` 对单条规格给出三种结果：

- `trusted`：信念为 `held`，且处于有效区间内，可以使用。
- `pending`：可疑或无法判断。在 `verify` 记下裁决之前不要使用。
- `absent`：没有这条规格，或它已经失效、越出有效区间。

`verify` 接受三态裁决，并且必须带理由：

| 裁决 | 写回 Neo4j 的结果 |
| --- | --- |
| `held` | 信念回到可信，扰动记录清空，有效区间继续开放 |
| `invalidated` | 信念改为已失效，`valid_to` 收到裁决时刻 |
| `unverifiable` | 信念改为无法判断，区间不关闭，召回时仍在待查集合 |

闭环是：变更触发，可疑标记，消费时语义校验，状态写回。

## 调用

- `SPECGRAPH_NEO4J_URI`，默认 `bolt://localhost:7687`
- `SPECGRAPH_NEO4J_USER`，默认 `neo4j`
- `SPECGRAPH_NEO4J_PASSWORD`，必填
- `SPECGRAPH_NEO4J_DATABASE`，默认 `neo4j`
- `SPECGRAPH_PROJECT`，可选

```python
from specgraph import consume, load_graph, recall, scan, verify
from specgraph.govern import create_spec

create_spec(
    ".",
    "auth-boundary",
    "module-design",
    "Auth module boundary",
    statement="Session handling stays inside the auth module.",
    anchors=["pkg/auth.py#Session"],
)

scan(".")
memory = recall(load_graph("."))
decision = consume(".", "auth-boundary")
if decision.status == "pending":
    verify(".", "auth-boundary", "held", "The session boundary still matches the code.")
```

## 固有局限

结构差分只能识别符号和签名变化，不能自行裁决深层语义规则是否被破坏。所以它只负责标可疑。

规格本体、关系和代码锚点需要调用方写入。没有锚点的模块设计不会被结构扫描碰到。

长期没有被消费的可疑标记会留在待查集合里，形成可见的记忆冗余。系统不会在后台把它们清掉。

扰动只沿 `IMPLEMENTS` 和 `DEPENDS_ON` 扩展一跳，不沿 `PARENT` 或 `REFERENCES` 继续追溯。JavaScript 与 TypeScript 的指纹来自声明抽取，不是完整语法树。
