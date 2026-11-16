# 看了那么多 Agent 评测文章，为什么依然搭不出自己的评测体系？

- 来源公众号：Gene化论
- 发布时间：Nov 14, 2026, 12:08 AM
- 原文链接：https://mp.weixin.qq.com/s?__biz=MzY4ODQ5MTgxNg==&mid=2247483697&sn=bff0defbd34bf4798ce255700156bebc&idx=1
- 本地抓取日期：2026-11-15
- 用途：评测体系设计参考（CIAO）；仅本地参考

---

听过很多道理，依然过不好这一生。
——韩寒《后会无期》

Agent 评测也有点像这样。

网上关于 Agent 评测的文章很多。单任务、多轮、工具调用、记忆、LLM as a Judge……读的时候，每一篇都觉得有道理。可真正轮到自己搭一套评测体系，还是不知从何下手。

Pexo 很早就采用了 ReAct 架构。团队早期开始做 Agent 时，也遇到了同样的问题：大家都知道评测重要，但一讨论具体怎么做，就觉得很难，无从下手。

看最终结果，好像解释不了中间的问题；单独测工具、记忆，又不知道这些分数和最后的效果有什么关系。

文章看了不少，回到自己的业务，仍然一头雾水。

这篇文章想从 Pexo 做 Agent 评测的心路历程出发，探索一套相对通用、能够落地实操的评测思路和方法。

## 0. Agent 评测为什么难

### 0.1 会讲兵法，就是名将了吗？

《明朝那些事儿》里，当年明月把名将的成长比作读小学，要过六个年级：

理论：先学兵法，懂得行军布阵。

实战：真正上战场，见识纸上没有的情况。

冷酷：面对战争的残酷，不再抱有天真的想象。

理智：局面再紧张，也不能被情绪牵着走。

判断：进还是退、攻还是守，关键时候得拿主意。

坚强：打了败仗，还能重新站起来。

六关之外，他还补了一项：运气。毕竟战场上，并不是所有事情都在自己的掌握之中。

Agent 的进化，跟名将的炼成之路，有些相似。

赵括从小熟读兵书，论兵头头是道；他爹赵奢却不认为他会打仗，甚至担心，让他领兵会断送赵军。

一个好的 LLM 基模，在 Chat 时也能出口成章、倚马可待；但真把它放到现实环境里完成任务，情况就复杂得多。

人有“纸上谈兵”，AI 也有“硅中论道”。

坐而论兵，可以对答如流；真正的战场，却瞬息万变。兵书只是基础，更重要的是结合战场上的变化，作出判断和决策，甚至提前预判。前线战况、后方补给、天气、地势、部队士气，都要考虑进去。命令发出去以后，还得知道部队执行得怎么样，局势又发生了什么变化。

![Image](https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNpLVOb59LhcsmvmRm6He16NTYibAGLVea6w4pxDCPQLRhzW4kjf1oRcC8Z5JnMczksdjUdiaaa4UOTgxTWXV9VLHd1UXkLJnJnfw/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=0)

<!-- image 1: https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNpLVOb59LhcsmvmRm6He16NTYibAGLVea6w4pxDCPQLRhzW4kjf1oRcC8Z5JnMczksdjUdiaaa4UOTgxTWXV9VLHd1UXkLJnJnfw/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=0 -->

图 1｜从谋划到作战
主帅的谋划，要放进整支军队的行动与反馈中检验。

Agent 也一样：做好推理输出只是基本能力。在实际运行过程中，上下文怎样组织、工具怎样使用、出错后怎样诊断与修复，都需要考虑进去。模型还要根据执行结果继续判断，决定下一步怎么做。

![Image](https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNpPNlaXYyXrzMxSL9qPgmAODhLILsaG1vfyP0jvianPUWOiaK3au2CT4Dk6LhnmPhx75iaZe1W96iahicTyrbKXgGdD2XFVVXsBAXsw/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=1)

<!-- image 2: https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNpPNlaXYyXrzMxSL9qPgmAODhLILsaG1vfyP0jvianPUWOiaK3au2CT4Dk6LhnmPhx75iaZe1W96iahicTyrbKXgGdD2XFVVXsBAXsw/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=1 -->

图 2｜从推理到行动
同一个模型持续参与判断，上下文、工具、执行环境与反馈共同影响任务结果。

这也是 Agent 评测难的根本原因：我们要检查的范围，从模型的判断与输出，扩展到了整套系统的运行和结果。

### 0.2 真正执行起来以后

假设你让 Agent 订一家今晚七点、四个人聚餐的餐厅，离公司近一点，人均不超过两百元。

① 预订真的成功了吗？

Agent 找到一家符合要求的餐厅，查询时还有空位，但提交预订时，系统却返回：这个时段已经订满。

接下来怎么办？它得重新找餐厅，同时记住七点、四个人、距离和预算这些要求。不能为了完成预订，就悄悄改成八点，或者换一家贵了一倍的。如果找不到符合条件的，就得回来问你愿意调整哪项要求。

找到另一家之后，也不能发出预订请求就告诉你“订好了”。返回的是“预订成功”，还是“等待商家确认”？最终确认的人数、时间和门店对不对？这些都需要核对。

从推荐一家餐厅，到真正订好一张桌子，中间还有不少事情要做。

因此，模型的工作不是出完最初的方案就完事了：工具每返回一个结果，它都可能需要再作判断——这个结果对不对，原来的计划要不要改，下一步该做什么。前面查了什么、做了什么，也会影响后面能够拿到的信息和采取的行动。

当然评测也得跟上。最基础的，需要检查工具调用的参数是否正确；再往下，要检查请求是否真正执行、预订是否得到确认；换了一家餐厅，还要检查原来的时间、人数、距离和预算等要求有没有被保留下来。

![Image](https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNqKHGLPhzlnNyBE0NGqf5ibLK4HezePoibQ53xcGQSBib4ia4icITnMcOMqodqHN2ND76RxHKF8PowytZgPG7Id5vyIJdlU5ynxumP8/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=2)

<!-- image 3: https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNqKHGLPhzlnNyBE0NGqf5ibLK4HezePoibQ53xcGQSBib4ia4icITnMcOMqodqHN2ND76RxHKF8PowytZgPG7Id5vyIJdlU5ynxumP8/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=2 -->

图 3｜订餐过程中的评测点与判断示例

查询时有位，不代表提交时仍然有位；提交了请求，也不代表预订已经确认。每一步都要检查实际结果，换方案时还要保留原有要求。

像预订时间是否为七点、人数是否为四人、返回状态是否为“已确认”，只要要求和执行记录中的字段明确，就可以写成程序，逐项比对并给出结果。

餐厅订满只是其中一种变化。工具还可能超时、返回权限错误，你也可能中途把四个人改成六个人。Agent 需要感知这些变化，再决定是核对当前状态、换一种办法尝试，还是回来找你确认，不能照着最初的计划一路执行到底。

同样是你到了餐厅却没有订到位，原因可能是 Agent 把“待确认”误当成成功，也可能是预订请求没有真正发出去，或者商家拒绝预订的消息没有传回来。冰山上的现象都一样，但冰山下的真实原因可能完全不同。

所以，如果只看最终的结果，我们或许能判断出对错、好坏，却不知道是怎么做对的、有没有走弯路、走岔路、走错路，也不知道是怎么出错的、该怎样修复。

② 条件都满足，就算选对了吗？

时间、人数、预订状态，还能拿要求和记录核对。但如果你再加一句：“今晚招待一位重要客户，找个适合聊天、又不太拘谨的地方。”事情就没那么容易评了。

![Image](https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNo8xFRbuN7VWicHzAHZzwdA1YmxUv348PXP58j1RUXqHqOicUibukw1gBnwMlzw1bhsImstvGiadCmGkaRVLLw7HFmD0DDmThloPsQ/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=3)

<!-- image 4: https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNo8xFRbuN7VWicHzAHZzwdA1YmxUv348PXP58j1RUXqHqOicUibukw1gBnwMlzw1bhsImstvGiadCmGkaRVLLw7HFmD0DDmThloPsQ/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=3 -->

图 4｜满足硬条件之后，怎样判断是否合适

Agent 推荐了餐厅 A，理由是“更方便与客户深入交流”。这个理由有没有回应你的需求？有没有忽略“不太拘谨”的要求？是否把未经证实的介绍当成事实？这些可以让另一个模型按照约定标准来检查。

③ 推荐理由说得通，实际体验就好吗？

吃完这顿饭，你可能会说：“确实安静，但服务员一直在旁边，聊得很拘谨。”也可能觉得：“虽然有些背景声，但座位私密，交流很舒服。”这些来自实际体验的判断，需要请参与者按具体问题反馈：交谈是否受干扰、氛围是否合适、是否愿意再次选择。

所以，Agent 评测不只是检查有没有做错，还要判断有没有做好。执行过程、最终结果、用户感受，都需要评价；出了问题，还得能找到原因。

这样看下来，评测似乎越来越难了。用户会怎么说，环境会怎么变，工具会返回什么，很难提前全部列出来；同一个任务，还可能有不止一种合理的完成方式。

如果只能不断找 Case、猜输入、补预期结果，就总有补不完的新情况：今天补上一个，明天又冒出几个。评测很容易变成追着问题跑。

难道我们只能这样，一个 Case、一个 Case 地补下去？

## 1. Agent 评测到底应该怎么做

### 1.1 又回到最初的起点：软件工程怎样解决这些问题？

心有猛虎，细嗅蔷薇。
——西格夫里·萨松

其实，以前做软件，也没有谁能把所有输入和运行状态都测一遍。

我们仍然有办法组织测试：先弄清系统由哪些部分组成、各自负责什么，再分别检查，最后放到一起验证。

软件里，这种由多个部分相互配合的系统很多，操作系统就是一个：调度、内存管理、文件系统、设备驱动，各管一部分，又彼此依赖。

诶，操作系统我熟啊，搞了十几年。

那做 Agent 评测，能不能也换个起点？先不急着枚举 Case、猜输入输出，而是从第一性原理出发，把 Agent 拆开看看：它究竟怎样运行，哪些部分参与其中，各自负责什么，又怎样配合？

结构和职责弄清楚了，再看每一部分应该测什么，以及组合起来以后该怎样验收。

又回到最初的起点。

古法编程时期，用户反馈“卡了”，我们会看日志、查指标、追调用链，必要时在复现环境里打断点。是在等磁盘，内存不够，还是线程得不到调度、hang 了？极端情况下，还得继续查是不是内核 Bug。

日志记录发生了什么，指标反映影响多大，调用链帮助定位卡在哪一环。这些可观测性（Observability）信息，为复现和验证原因提供线索。

这其实就是 Debug。

当然，也不能所有问题都等用户来发现。在上线之前的开发阶段，我们不会无脑开发几天，然后整体编译、跑起来看看有没有问题。

先把函数或模块里的独立逻辑拿出来检查：给定输入，结果是否正确；遇到边界条件、错误返回，又该怎样处理。需要时，把外部依赖替换成可控的 Mock，避免其他因素干扰。这就是单元测试（Unit Testing）。

单独测过，还要接起来。文件系统向设备驱动发出的请求能不能被正确处理，设备返回的错误能不能传回调用方，几个模块对数据格式、状态和调用顺序的理解是否一致？这就是集成测试（Integration Testing）。

把完整系统放到接近实际使用的环境里，检查功能、性能和稳定性是否满足要求，这是系统测试（System Testing）。沿用户的一项操作，从输入一直验证到最终结果，则是端到端测试（End-to-End Testing）。两者可以结合使用。

如果因为每个模块都通过测试，就认定整个系统一定没有问题，就犯了“合成谬误”。各部分分别成立的结论，未必对组合后的整体成立。共享资源、并发执行、模块之间的配合，都可能带来新的问题。

修复后重跑相关用例，确认问题消失、其他功能没有被改坏，这是回归测试（Regression Testing）。将检查接入 CI/CD，发布后再用监控和实际运行数据发现问题，结果回到下一轮开发，就形成了 DevOps 的持续反馈。

回头看，我们刚做 Agent 评测时，被模型、Prompt、工具和记忆绕住了，反而没及时想到：拆模块、测接口、查日志、做回归，这些办法以前就用过。

想给 Agent 做好学习和迭代，我们自己倒先得补上一次“迁移学习”：把过去的经验重新翻出来，看看哪些还能用，哪些需要调整。

功不唐捐。

决策交给模型以后，输出不总有唯一答案，过去的经验需要调整，但仍然可以作为起点。

把这套思路用到 Agent 上，先从它最基本的运行过程——Agent Loop——开始。

### 1.2 从 Agent Loop 拆出四组件

振叶以寻根，观澜而索源。
——刘勰《文心雕龙·序志》

先看一次最基本的 Agent 运行循环：当前信息被组装成上下文，模型据此生成回复或工具调用；工具结果写回运行状态，再参与下一次判断。任务完成、确认失败或需要人介入时，循环结束或暂停。

![Image](https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNrX3mibaynX7BPduyicWuPJvJyOlWiaPaUqLuicSXwzJ7ibW0ic5Nrn3m2rJTjthvTvIjCts2WvSicIFUBu2Rx7WO7JmO0gszd7gZzmR0/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=4)

<!-- image 5: https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNrX3mibaynX7BPduyicWuPJvJyOlWiaPaUqLuicSXwzJ7ibW0ic5Nrn3m2rJTjthvTvIjCts2WvSicIFUBu2Rx7WO7JmO0gszd7gZzmR0/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=4 -->

图 5｜Agent 运行循环（简洁版）

为了设计评测，我们按运行职责把这个循环拆成四部分。这里借用 MECE（互不重叠、整体无遗漏）来检查划分是否清楚；它是组织问题的方法，并不能证明四部分就是唯一、完备的 Agent 架构。

注：下面是本文采用的评测职责边界，不要求代码恰好有四个模块；同一次失败也可能涉及多个职责。

LLM Core：理解与决策。模型理解当前输入，决定下一步，并生成回答或工具调用。

Context：判断依据与组装。检查模型实际收到哪些信息，以及信息怎样被选取和整理。系统里存着一条记忆，不等于这次调用真的带上了它。

Action Interface：请求与结果的传递。模型选好工具后，接口负责解析参数、找到工具、发出请求，再收回结果或错误，写入约定的运行状态。

Environment：实际交互的外部依赖。包括工具背后的服务、文件与数据库，以及网络和计算资源。它们的能力、状态和故障同样影响任务。

注：工具返回的预订记录是 Context 的信息来源，预订系统的实际记录属于 Environment。发起查询是接口职责，外部系统处理查询是服务职责。

本文据此组织四组检查：

LLM Core × Context × Action Interface × Environment

注：乘号表示各部分相互依赖，不是四项评测分数相乘的计算公式。

测模型，可以固定上下文和工具返回；测上下文组装，可以固定历史与用户输入；测接口，可以接可控的 Mock 服务；测外部工具，则用固定请求检查返回质量与可靠性。

还需要继续拆到能出测试题的粒度。例如 Context 出问题，可能是指令矛盾、状态过期，也可能是组装时漏掉了信息，测法各不相同。

#### 1.2.1 每个组件继续按什么依据拆？

细拆时先选维度，再查重叠和遗漏。四组职责不同，不必拆成同样多的子项，也不必让所有分支一样深。

LLM Core，是 Agent 的决策大脑，按完成决策所需的功能来拆。

模型根据当前上下文生成后续输出。评测时可以检查三个问题：有没有理解相关信息，决策是否合理，输出是否准确表达了决策。

据此划分感知与理解（Perception）、推理与决策（Reasoning）、输出生成（Generation）。例如漏看人数变更、决定错误、工具参数写错，需要分别设计用例。

注：这是评测的功能视角，不是模型内部三个独立模块；仅凭一次错误输出，还不能断定问题发生在哪一步。

Context，先按信息的作用拆，再单独看组装机制。

先按信息在当前调用中的用途分三类：

指令（Instructions）：任务规则、系统提示、行为约束。

状态（State）：对话历史、记忆、工具结果、当前任务进展。

能力说明（Capabilities）：有哪些工具、各自能做什么、参数怎样填写。

再看组装机制（Assembly）：需要的信息有没有取回，顺序与优先级是否正确，截断或压缩是否丢了关键内容。它决定下一次模型实际看见什么。

Action Interface，先按信息的方向拆，再按传递过程细分。

按信息流向分成两条路径：

输出路径（Output Path）：解析（Parse）→ 路由（Route）→ 执行调用（Execute）

输入路径（Input Path）：接收结果（Capture）→ 转换结果（Transform）→ 写入运行状态（Inject）

输出路径解析模型的工具选择与参数，路由到工具并发起调用。输入路径收集结果或报错，转成约定格式，再写入状态。这里的 Inject 止于结果入库或入消息；Assembly 决定哪些内容进入下一次模型调用，两者可以分别留证、分别测试。

模型提出的继续、停止或重试决策，在 LLM Core 里评；执行代码是否遵守终止条件、重试策略、权限和资源限制，则在本文归入 Action Interface 的运行控制职责。它们也需要测试，不能只检查请求能否发出去。

![Image](https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNoeW45ibl6tt3JCMx7pgx5FawCibO6Q3dGGjjPXpyic0JQX02KIb9OicXGIlqxia8NiajibjLBlNoIJATiax3TIUItlZqLTwAMIZZ3Wu1E/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=5)

<!-- image 6: https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNoeW45ibl6tt3JCMx7pgx5FawCibO6Q3dGGjjPXpyic0JQX02KIb9OicXGIlqxia8NiajibjLBlNoIJATiax3TIUItlZqLTwAMIZZ3Wu1E/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=5 -->

图 6｜行动接口的请求发出与结果回收

图：智能体运行循环（行动接口展开版）。调用请求向外，执行结果向内；外部服务的实际执行位于行动接口之外。

Environment，按 Agent 对外部依赖的使用方式来拆。

有的是请它办事，有的是从中读写信息，还有的是支撑整个系统运行：

外部服务（External Services）：搜索、生成、执行等工具背后的能力，检查返回质量、可用性与超时等问题。

数据资源（Data Resources）：文件、数据库、对象存储，检查数据是否存在、是否过期、读写是否一致。

基础设施（Infrastructure）：计算、网络、消息队列等，检查容量、延迟与运行稳定性。

数据库记录读取失败，与记录已经返回却没进入模型输入，是两个问题。前者查访问链路与外部依赖，后者查结果写入和上下文组装。

注：同一外部系统可能同时提供服务和保存数据，应按所测职责分类；不可控的环境变化也要记录。

四组职责及其细分如下：

![Image](https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNpcLDAPGbCuRcaV46hA8BvMmNrkdyvkEw03Dia59SjdG4IibUW6ye6giaamCvyUebUVaiaDwibKpZzXL2icXZBpQ7RiccOLAOhEfmy8qQ/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=6)

<!-- image 7: https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNpcLDAPGbCuRcaV46hA8BvMmNrkdyvkEw03Dia59SjdG4IibUW6ye6giaamCvyUebUVaiaDwibKpZzXL2icXZBpQ7RiccOLAOhEfmy8qQ/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=6 -->

图 7｜Agent 四组件与职责拆解

任务变更是否被理解、状态是否过期、返回是否被截断、服务是否可用，都可以落到具体测试题。

### 1.3 拆完还要组回来：CIAO 怎样推导出来

横看成岭侧成峰，远近高低各不同。
——苏轼《题西林壁》

组件测试回答了局部能力问题。多组件接起来后，信息是否正确传递、最终是否满足用户目标，还需要继续检查。

#### 1.3.1 C：Component，各组件能不能胜任？

尽量固定其他条件，分别检查模型、Context、接口和环境依赖，找出它们的能力与失败边界。

这与单元测试的思路相通，重点是让一次测试的问题足够明确、结果能够复现。

#### 1.3.2 I：Integration，组合起来，事情能不能办成？

I 关注多个组件如何配合。可以测有明确输入和产出的子流程，也可以测完整任务，检查信息传递、状态衔接和集成后的产出。

以视频制作为例，可以按产出划分脚本规划、素材生成、剪辑组装等子流程。每个流程都可能包含多次模型调用、工具执行和结果回流。

![Image](https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNpVwGQxbkSHOO78pBcu5OQuaXfDsSt1kEdpRiaNDT8AMpWkibpiajy7qZF1N5psCecG6kW8pfGRhnOhHo208DV53V4Fa9CPYm20rA/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=7)

<!-- image 8: https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNpVwGQxbkSHOO78pBcu5OQuaXfDsSt1kEdpRiaNDT8AMpWkibpiajy7qZF1N5psCecG6kW8pfGRhnOhHo208DV53V4Fa9CPYm20rA/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=7 -->

图 8｜视频制作的子流程与产出

图：视频任务的子流程评测示例。各段检查输入到产出的完整配合，整条链还要做端到端评测。

子流程

可以检查什么

脚本与分镜

卖点是否完整，段落与镜头安排是否满足要求？

素材生成

素材是否符合分镜，指定参考和关键属性是否保留？

剪辑组装

素材是否使用正确，画面、字幕和音频是否完整衔接？

测素材生成时，可以先给一份已核对的分镜，排除规划错误的干扰；再跑完整任务，检查流程之间能否正确传递信息。

C 与 I 是多对多的关系。一个流程需要多个组件，同一个组件也会参与多个流程。子 Workflow 是可选的集成评测单元。

同一个 LLM Core 既参与脚本规划，也参与素材检查；同一个 Action Interface 既调用生成服务，也调用剪辑工具。一次素材生成又需要 Context、模型、接口和外部服务共同配合。因此，组件测过了，还得看它在具体流程中的表现。

注：阶段是评测的观察点，不是规定 Agent 必须遵循的唯一流程。只要满足任务及必要约束，通过其他有效路径完成，也应当被认可。

#### 1.3.3 O：Outcome，完成得怎么样，对用户有没有价值？

O 回到用户目标，验收最终交付。例如成片能播放，但日期错了；内容正确，但用户反复纠正十几轮才完成。这些都要在最终效果、使用体验与经济性上评价。

I 与 O 可以使用同一次运行的证据：I 查组件配合和信息传递，O 查交付是否满足目标，不必另跑两遍。真实业务价值仍需实际用户与运行数据验证。

#### 1.3.4 A：Attribution，出了问题，究竟该改哪里？

C、I、O 告诉我们表现怎样，还需要解释为什么。A：Attribution，归因，负责收集证据、提出原因假设，再用对照或重放验证，贯穿这三个视角。

它有独立的职责，但不单独夹在某两层之间。哪里发现问题，就从哪里开始查，必要时跨层追下去。

模型遵循指令变差、素材生成失败、用户反复修改，都可以成为归因的起点。需要顺着实际输入、请求、返回和产物追查，不能把“任务失败”直接等同于“模型不行”。

例如怀疑上下文压缩丢了人数变更，可以固定任务和历史，比较压缩前后的实际输入，再重放后续执行。一次改善只是线索，还要检查能否复现、是否存在其他原因。

![Image](https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNpISTfPZHia5FiaJx6ChHc0tKlmZuDlQ6VLrF2rSc2mH7eeBbhsavqlryqtY3dO49HLicGon5zZslrkPPUXibTykGib7HibqO5SOd3tI/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=8)

<!-- image 9: https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNpISTfPZHia5FiaJx6ChHc0tKlmZuDlQ6VLrF2rSc2mH7eeBbhsavqlryqtY3dO49HLicGon5zZslrkPPUXibTykGib7HibqO5SOd3tI/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=8 -->

图 9｜CIAO 评测框架：C、I、O 与贯穿其中的 A

图：A 使用 C、I、O 的证据进行诊断，再通过实验验证原因。它不位于 I 与 O 之间，也不要求等任务结束才开展。

我们把这四个视角称为 CIAO（请不要连起来读“次奥”）：

视角

主要回答

C · Component

各组件本身的能力和边界是什么？

I · Integration

组件配合后，任务过程和产出表现如何？

A · Attribution

偏差从哪里来，原因怎样验证？

O · Outcome

是否满足目标与约束，是否真正有用？

C、I、O 是互补的评测视角，A 贯穿其中。字母顺序不代表四层分数，也不规定执行顺序。

#### 1.3.5 两条路径：主动优化与问题排查

同一套框架，可以从改动出发向上验证，也可以从失败结果向下追查。以下用“用户改了人数，系统仍沿用旧值”这一类问题说明。

自底向上：组件变好了，对最终效果是否有提升？

这条路径，很适合用在开发与上线前的验证阶段。

![Image](https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNrv0ZtMc6zo6scNZ62VibXDxIKIXRgu2rbuE0jM9sq9Zyc6ONUrlQo5DQj4w2K0lBqqn6ib8frgc6fiaf80H1M9wUbJQTgTP1rfDU/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=9)

<!-- image 10: https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNrv0ZtMc6zo6scNZ62VibXDxIKIXRgu2rbuE0jM9sq9Zyc6ONUrlQo5DQj4w2K0lBqqn6ib8frgc6fiaf80H1M9wUbJQTgTP1rfDU/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=9 -->

图 10｜主动优化：从组件改进到最终效果

图：自底向上验证改进。组件改善后，还要检查它能否传递到集成表现和最终效果；A 根据各层证据解释变化。离线验收通过后，仍需按发布流程验证线上表现。

假设我们改进了上下文组装。先在 C 固定同一份对话历史，检查“四人改为六人”有没有进入模型输入，时间与预算是否保留。

再到 I，运行查询和预订子流程，检查工具是否使用六人参数，返回结果是否进入后续判断。

如果输入已经改对，预订仍用了旧人数，A 就要继续查模型输出、参数传递和状态更新，验证问题出现在哪一步。

最后在 O 比较最终预订是否满足要求，以及耗时、费用和用户纠正次数有无变化。新旧版本使用同一批任务和标准，重复运行并检查回归。

自顶向下：线上 Case 出了问题，究竟应该改哪里？

线上问题往往从结果出现：用户说“我明明改成六个人，订的却还是四人”。

![Image](https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNqqtJkafQxJ0KEHkPq8DAWrAaFZjs4EN6FIczkpSyE2aM291FFtZT3DEZOoUkSKG81pkf7UyXw5dzwIaBiasaPyTqyHacGxticsw/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=10)

<!-- image 11: https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNqqtJkafQxJ0KEHkPq8DAWrAaFZjs4EN6FIczkpSyE2aM291FFtZT3DEZOoUkSKG81pkf7UyXw5dzwIaBiasaPyTqyHacGxticsw/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=10 -->

图 11｜问题排查：从结果偏差追到组件

图：自顶向下排查问题。先确认结果中的偏差，再回看任务过程，逐步缩小到可复现的组件问题；A 全程参与原因验证。修复后回到上一张图，自底向上做回归与效果验收。

O 确认了人数不符；回到 I 的执行记录，发现查询和预订请求都沿用了四人。

A 再核对用户消息、运行状态与模型实际输入。如果消息中有六人，而组装后的上下文仍是四人，就得到一个可验证的假设。

到 C 固定这段历史，单独运行组装机制。能否复现遗漏？改动后是否消失？若不能，就继续查其他原因。

修复后再沿 C→I→O 重跑相关评测和回归，确认最终预订正确，再按发布流程上线观察。

从线上问题向下排查，再向上验证改进，两条路径接进同一个 DevOps 反馈过程。

注：这是两种典型使用方式，不是阶段限制。上线前也会从失败案例向下排查，线上同样可以主动优化。

各层要检查什么有了，具体由谁判断好坏？

### 1.4 有了框架，具体由谁来评？

操千曲而后晓声，观千剑而后识器。
——《文心雕龙·知音》

#### 1.4.1 评测标准与证据

要求七点四人、人均两百以内，订成八点六人、价格翻倍，就不能算完成任务。用户要招待客户还是朋友聚餐，也会影响“合适”的含义。

评测前先确定成功标准（Success Criteria）：必须满足哪些条件，哪些质量需要进一步比较。比较两个版本时，任务与标准应保持一致。

Agent 回复“订好了”只是它的陈述；预订系统里的状态、时间、人数和门店，才是核验交付的依据。

一次运行的执行轨迹（Trace）记录模型输入、输出、工具请求与返回；最终产物和环境状态提供结果证据。两者需要核对，不能只看 Agent 自己的总结。

![Image](https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNptrcXPNckibRUgyc4eIgRFMIMJYbaiaX9x2ib796S01iblTR1cpmELrywspxqgpOjdicCibk2QTQj2FlrhENhAmsA8eN42cJg4UjqHY/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=11)

<!-- image 12: https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNptrcXPNckibRUgyc4eIgRFMIMJYbaiaX9x2ib796S01iblTR1cpmELrywspxqgpOjdicCibk2QTQj2FlrhENhAmsA8eN42cJg4UjqHY/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=11 -->

图 12｜评测标准、运行证据与评价器

标准来自任务，判断依据来自实际运行。检查中间决策，要看当时的上下文与执行记录；验收任务完成情况，要核对最终产物和环境状态。Grader 根据所评问题，使用相应的标准与证据。

查询时有位，提交时订满，不能仅凭预订失败就认定最初的选择错误。要看当时可获得的信息，以及失败后是否采取了合理行动。

中间决策按当时的信息评价，最终交付按结果验收；原本能查到却没查的信息，也应纳入检查。

“方案是否周全”“表达是否清楚”需要 Rubric（评分规则），说明维度、分档和示例。视频场景的具体规则，留到后续人评文章展开。

每条判断都要说清：问什么、依据什么、按什么标准评。证据不足就标记无法判断。

#### 1.4.2 程序、模型和人，分别评什么？

订餐案例中的字段核对、推荐理由和实际体验，分别适合交给程序、模型和人。负责这些判断的机制，我们称为 Grader。

把它们用起来，还需要解决一个问题：怎样确认这些裁判评得可靠？

程序评测：Code as a Judge。

字段是否齐全、文件能否打开、数据库是否写入了指定记录，都可以交给程序核对。这类规则容易重复执行，成本也较低。

但要求生成报告却只检查文件存在，哪怕文件只有“任务完成”四个字也会通过。程序执行正确，不等于规则覆盖了真正的要求。

模型评测：LLM as a Judge。

要点有没有遗漏、两段话是否同义、回复是否答非所问，可以把任务、证据和评分规则交给模型逐项判断；图像、音频和视频则需要具备相应能力的多模态模型。

但会生成内容，不代表一定会评内容。模型可能漏看证据，也可能被一段漂亮的表达说服。它给了一个分数，还写了一段解释，这个判断本身同样需要验证。

评报告时，不妨把“好不好”问具体：“有没有讨论风险？请指出对应段落，判断它是否解释了风险怎样影响结论。”回答有了依据，才能复核。

用人工核对过的案例校准：认真分析风险、只写一句“投资有风险”、完全没提风险，模型能否分清？重复评几次，判断是否稳定？

比较两个方案还可以交换展示顺序。如果模型总选排在前面的，就要检查位置偏差。

没有原始数据或来源，模型应能回答“无法核实”，不能凭文字像不像真的来打分。

人工评测：Human as a Judge。

视频尤其依赖人的判断：技术规格通过了，仍可能节奏拖沓、情绪不对。专业创作者检查镜头与剪辑，目标观众反馈是否看得懂、愿不愿意看，两者回答不同的问题。

人工也要校准：同一任务使用同一套要求和材料，先独立判断，再讨论分歧。任务完成度和审美偏好应分别记录，不能混成一个平均分。

我们已梳理 Video Agent 的人工评分机制，后续会另文介绍维度、分档、问题证据与分歧处理，聊聊怎样从“会审美”走到“会评美”。

三类 Grader 可以共同评价一次任务：程序检查字段，模型检查语义，人工复核专业质量与体验。报告保留各项结果和证据。

![Image](https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNptOpXVf3hNR99aT9ib4Wvbb4uCu99byjiaBZRG0JMl9U2IJ3o3C4AuicAGySFibibkx6J82wAnOeNk2Jbic4cYdc0icOibZnfwUWq6Ccw/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=12)

<!-- image 13: https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNptOpXVf3hNR99aT9ib4Wvbb4uCu99byjiaBZRG0JMl9U2IJ3o3C4AuicAGySFibibkx6J82wAnOeNk2Jbic4cYdc0icOibZnfwUWq6Ccw/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=12 -->

图 13｜程序、模型与人工评价的协作

三类 Grader 按问题分工，可以共同评价同一次任务。报告保留分项结果和证据，不因某一种裁判通过，就自动放行整个任务。

#### 1.4.3 评测这件事，也可以交给 Agent 来做

前面说的模型评测，通常是把任务、证据和评分规则准备好，再交给模型判断。如果证据还没找齐，评价者能不能自己去查？这就可以用到 Agent as a Judge：让负责评测的 Agent 调用工具、收集证据，再按标准作出判断。

执行任务的 Agent 说“订好了”，负责评测的 Agent 可以按预订编号查询确认记录，用程序核对时间、人数和门店。核查既要看当前状态，也要保留对应时间点的记录，避免把事后的取消误当成当时没订成功。

它还可以调取对话和工具记录，让模型检查换餐厅时有没有遗漏要求。缺证据就继续查，查不到就标记无法判断或交给人复核，最终给出分项结论与依据。

评测 Agent 增加的是主动取证和组织检查的能力。它也会漏看、误判，需要用已核实的案例检验；客户的实际体验仍需人的反馈。

还有一类常见问题：多轮交互形成的历史，怎样放进评测？

### 1.5 多轮对话，怎样化归成一次评测？

单轮对话，拿着输入和输出，还比较容易判断。到了多轮，前面说过什么、后来改了什么，都会影响当前这一轮。只把最后一句话拿出来，往往连题目都不完整。

比如，用户先说“今晚七点、四人、人均两百以内”，后来把四人改成六人，又要求只筛选餐厅、先别预订。最后问：“下一步怎么做？”

Agent 得按六人查询，保留七点与预算约束，并停在筛选阶段。如果还按四人查，或直接提交预订，就出了问题。

这种场景，其实在我们古法编程的时候也遇到过：

for (uint i = 10; i >= 0; i--) {
}

假设这里的 uint 是无符号整型。本来想从 10 倒数到 0。前面几轮都正常，到了 0，再减一次，数值却回绕成这个类型能表示的最大值。i >= 0 始终成立，循环也就不会按预期结束。

当然，这个问题静态检查也可能发现。这里想说的是，调试时我们会关心程序当前处于什么状态。要复现后面这一段行为，不一定每次都从 10 跑起，也可以直接把 i 设为 0，观察接下来会发生什么。

无独有偶，多轮对话评测，也可以借这个思路。

我们目前的做法，就是通过构造多轮对话历史及其结果，把多轮问题化归成从一个固定现场开始的单次评测。

把前面几轮用户说的话、Agent 的回复，以及相关工具结果准备好，再输入当前这一轮的问题。这样，评测的就是：在这段历史形成的上下文里，Agent 接下来的回应和行动是否正确。

![Image](https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNp7bZibVZP3Yn87vUvf1TxQ5eNVBwibK65iaSncpfsOWZ7YFhZYu5jHVAwpxxRnd6bJfu0ZGhDGvibUzyxDmxHkmKBDqArKiaCiaicpP4/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=13)

<!-- image 14: https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNp7bZibVZP3Yn87vUvf1TxQ5eNVBwibK65iaSncpfsOWZ7YFhZYu5jHVAwpxxRnd6bJfu0ZGhDGvibUzyxDmxHkmKBDqArKiaCiaicpP4/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=13 -->

图 14｜固定历史，评测当前这一轮

图：把历史与当前输入固定下来，评测当前请求的处理结果。这里的一次评测可以包含多次模型调用和工具执行。

构造历史时，可以准备三类用例：

① 给一段完整历史，看能否正确延续。

把初始需求、人数变更和“先别预订”放进历史，当前只问“下一步怎么做”。检查是否按六人筛选、保留时间与预算，且没有提交预订。

② 只改一条历史，看行为是否随之改变。

其他条件不变，只把“六人”换成“八人”，检查查询参数是否相应变化。这样可以判断 Agent 是否使用了这条历史。

③ 故意放入错误，看能否主动纠正。

最新要求是“只筛选，先别预订”，上一轮 Agent 却计划直接提交。当前不点明错误，看它能否核对最新要求并纠正。

也可以放入工具失败但 Agent 误报完成、用户改了人数但摘要仍保留旧值的 Bad Case，检查它会不会重新核对证据。

预期行为要事先说清：有证据的错误应纠正，证据不足时应查询或澄清，不能要求 Agent 猜评测者没有提供的信息。

这样，正常推进、要求变更和前序错误都可以放进历史，检查当前这一轮怎样处理，不必为每个场景都从第一轮重新聊起。

构造历史测的是“给定这些信息，接下来能否处理正确”；真实多轮执行还要测“这些信息能否被正确保存并带入当前上下文”。

后一种需要实际运行会话保存、记忆更新、摘要和压缩机制。手工提供完整历史，可能恰好绕过了会丢信息的环节。因此，构造历史用于可控复现和回归，完整交互继续检查状态维护与任务执行。

### 1.6 真要动手，先从哪里开始？

框架决定检查哪些问题，Case 提供实际验证的材料。有了框架，可以先拿一条团队最近遇到的问题跑通。

例如用户改了要求，Agent 仍沿用旧值。留下用户原话、变更、实际上下文、工具结果和最终交付，再用前面的历史构造方法复现。

将它写成可重复执行的测试用例（Test Case），明确条件、标准与证据：

内容

示例

要测的问题

用户修改关键要求后，Agent 是否按新要求执行

初始条件

固定对话历史、输入素材和相关工具状态

当前输入

用户的新要求，以及需要执行的任务

成功标准

新要求在结果中生效，其他仍有效的约束没有丢失

判定依据

实际模型输入、工具请求与返回、最终产物

Grader

明确字段用程序核对，语义要求按规则交给模型或人工

运行记录

系统与模型版本、规则版本、耗时、成本及分项结果

负责人

谁维护用例，谁跟进失败，怎样确认修复

这一条跑通后，再补相关失败、正常和边界案例，检查修改是否影响其他场景。

Eval Harness（评测运行系统）负责准备输入与环境、运行被测 Agent、收集证据、调用 Grader 和生成报告。它与被测 Agent 自己的Harness承担不同职责。

![Image](https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNrSWEo4xhgILC8raxUjNLof7V5vkPqcfUc0icmVrxOic1ZUUuBNVVB6Hr8AnS37K9BUnfsRgkzCklUkEkn6krdFOz4WiaedJW1kuE/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=14)

<!-- image 15: https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNrSWEo4xhgILC8raxUjNLof7V5vkPqcfUc0icmVrxOic1ZUUuBNVVB6Hr8AnS37K9BUnfsRgkzCklUkEkn6krdFOz4WiaedJW1kuE/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=14 -->

图 15｜一轮可复现的评测运行

图：一条最小评测运行链路。每次实际执行是一轮 Trial，同一条 Case 可以运行多轮。

比较版本时，任务、规则和可控初始条件要一致，不能让新版本复用旧版本留下的产物。还要注明工具使用真实服务还是Mock：前者包含服务变化，后者只能证明模拟条件下的表现。

前面说名将也需要运气。Agent 同样可能恰好成功一次，所以同一条 Case 要重复运行，比较稳定性，并保留样本量、分项结果和失败证据。

pass@k看k次尝试至少成功一次的概率，pass^k看k次全部成功的概率。两者回答不同问题，具体用法可参考Anthropic的介绍。

反复用来调 Prompt 和修代码的案例属于开发集。正式验收需要独立测试集，不能再拿已经针对性修改过的题证明泛化；校准 Grader 的案例也应注明用途。

组件改动先跑局部测试，发布前再跑完整任务与回归。线上失败留证、复现，修复后加入回归集，评测才会成为日常开发的一部分。

## 2. 用一条视频，把评测跑一遍

纸上得来终觉浅，绝知此事要躬行。
——陆游《冬夜读书示子聿》

下面用一条实际制作的视频，把流程定位、组件检查和重评跑一遍。

我们做了一条十五秒的蓝色随行杯广告，在组装时预埋了一个素材版本错误，用它演示一次完整的评测、排查和修复。

### 2.1 先看要求，再看视频

这次的要求很简单：做一条十五秒的竖屏产品广告，前五秒展示杯子，中间五秒展示细节，最后五秒用产品画面和终卡收尾。两处展示文字分别是“随身一杯”“日常相伴”，终卡必须写“周五开售”。

产品虚构，无品牌或性能宣称。

先看问题成片。

![图16]()

<!-- image 16:  -->

已关注

Follow

ReplayShareLike

Close

观看更多

更多

退出全屏

切换到竖屏全屏退出全屏Gene化论已关注

Share Video

，时长00:15

00:00/00:15

切换到横屏模式

继续播放

进度条，百分之0

Play

00:00

/00:15

00:15

倍速

全屏

倍速播放中

0.5倍 0.75倍 1.0倍 1.5倍 2.0倍

超清 流畅

Your browser does not support video tags

看了那么多 Agent 评测文章，为什么依然搭不出自己的评测体系？

观看更多

Original

,

视频 1｜问题成片：终卡误用了“周六开售”
问题成片 · 故障注入演示

如果只检查有没有生成文件、视频能不能播放，这条片子很容易通过。但对照原始要求，就会发现最后的终卡不对：要求写“周五开售”，视频里却是“周六开售”。

![Image](https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNouZ4XBLp4oY4RmOxkc8WFBzcubRiaYnuYW7G03lp8yaM1zdUK4QMC7kdYcCrYzouibV4kOtz4brXKIxoBLpw2N3353GficAUwIDM/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=15)

<!-- image 17: https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNouZ4XBLp4oY4RmOxkc8WFBzcubRiaYnuYW7G03lp8yaM1zdUK4QMC7kdYcCrYzouibV4kOtz4brXKIxoBLpw2N3353GficAUwIDM/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=15 -->

图 16｜要求素材与问题成片的日期对照

这是 O 发现的交付偏差：要求“周五开售”，成片却写“周六开售”。先记下现象，再沿制作过程追查。

### 2.2 从成片往回查：错误从哪一步开始？

先进入 I 视角，把本次制作按输入和产出划成四个可检查的子流程。需求与时间线由我们预先整理，后续使用生成服务和本地制作脚本。I₁—I₄ 是本案例的观察单元，不是四个新的框架层级。

![Image](https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNr8HbEbRib9J1x796oUQjI8rBbqvzz5mf0T93MicDzCd1bRDm3WppiasWBLYDMibMR915SqnaeK1oQlHRwuNUZyIDIzI6icr1dangfU/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=16)

<!-- image 18: https://mmbiz.qpic.cn/mmbiz_png/22ickpP4zRNr8HbEbRib9J1x796oUQjI8rBbqvzz5mf0T93MicDzCd1bRDm3WppiasWBLYDMibMR915SqnaeK1oQlHRwuNUZyIDIzI6icr1dangfU/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=16 -->

图 17｜从视频子流程定位到素材路由组件
只沿“终卡日期”这条证据链定位。标为符合要求的是这一个检查项，不代表该流程的全部质量都通过。

“周五开售”在 I₁ 确定，在 I₃ 成为终卡画面，最后由 I₄ 选取并渲染。因此，这个元素经过多个流程，不能看到日期错误就直接判定某一环节有问题。

逐步核对：制作要求与时间线写的是周五；指定终卡 endcard_v1 的画面也是周五；组装请求仍引用 v1。偏差首次出现在组装请求转为实际渲染输入时。

目录中还有一份旧终卡 endcard_v0，只有日期写成周六。实际解析记录显示：请求 v1，本地适配器却选了 v0。问题由此缩小到 I₄ 内部的素材选取步骤。

![Image](https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNrTPjTmUJZOWDawmCgIN1A2xauKibBQLt2CY46k0Rhh1KjKIZnRUB3CaCYoVN5jdylpxr4okgDqJFNmbRCsaLYePIctLRxx5eEs/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=17)

<!-- image 19: https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNrTPjTmUJZOWDawmCgIN1A2xauKibBQLt2CY46k0Rhh1KjKIZnRUB3CaCYoVN5jdylpxr4okgDqJFNmbRCsaLYePIctLRxx5eEs/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=17 -->

图 18｜组装请求与实际素材选取的追溯

再从 I₄ 下钻到 C：在本案例的职责边界里，本地素材解析适配器负责把标识路由到文件，归入 Action Interface；渲染器属于外部执行服务。固定请求、素材注册表和文件，只测这段映射行为。

正常模式下，v1 请求得到 v1 文件；开启预埋错误后，同一请求得到 v0。实际文件哈希与渲染输入记录，验证了这条错误映射。

A 贯穿了这个过程：从成片偏差提出假设，沿子流程找证据，再隔离组件做对照。这里已知故障注入点，演示的是验证方法，不是盲测自动诊断能力。

### 2.3 恢复正确映射，再验证一次

关闭这处错误映射后，我们从同一份请求独立重新渲染，留下新的解析与执行记录。

C 检查新版、显式旧版和不存在版本等输入的解析行为；I 重新运行组装，核对素材、时间线和音轨；O 再检查成片的日期、时长、画幅和两处文案。

![图20]()

<!-- image 20:  -->

已关注

Follow

ReplayShareLike

Close

观看更多

更多

退出全屏

切换到竖屏全屏退出全屏Gene化论已关注

Share Video

，时长00:15

00:00/00:15

切换到横屏模式

继续播放

进度条，百分之0

Play

00:00

/00:15

00:15

倍速

全屏

倍速播放中

0.5倍 0.75倍 1.0倍 1.5倍 2.0倍

超清 流畅

Your browser does not support video tags

看了那么多 Agent 评测文章，为什么依然搭不出自己的评测体系？

观看更多

Original

,

视频 2｜恢复成片：终卡改回“周五开售”
修复后重新渲染的成片

![Image](https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNqdTXyASibdbFPpAnfEqhxlogicLNIR5ibtkFve0CPIIyICOwtsHVzpVrxNT7ApA7iaVZhjY9z87HMXG9cGJlUN433hH1VAYLhkz9s/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=18)

<!-- image 21: https://mmbiz.qpic.cn/sz_mmbiz_png/22ickpP4zRNqdTXyASibdbFPpAnfEqhxlogicLNIR5ibtkFve0CPIIyICOwtsHVzpVrxNT7ApA7iaVZhjY9z87HMXG9cGJlUN433hH1VAYLhkz9s/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=18 -->

图 19｜故障版与恢复版的同帧对照

实际检查结果

三版成片均为15秒、1080×1920、30fps，450帧完整解码通过。

三版前10秒的300帧逐帧相同，解码后的音频也相同；差异被限制在末尾5秒。

修复版独立重新渲染，与正常对照版全部450帧一致。

正常模式6项解析检查通过；另6项故障注入检查确认错误按预期触发。三版共27个抽样帧的文字均符合各自预期，故障版终卡确为“周六开售”。

生成镜头原始尺寸为720×1280，交付时放大至1080×1920；这里检查的是技术规格和指定文案，没有给审美或投放效果打分。

从 O 发现偏差，经 I 定位流程、C 验证映射，再向上重跑验收，这就是两条路径的一次往返。

### 2.4 这个例子能说明什么？

这个案例说明了怎样凭证据追查一处组装错误，并验证恢复后有没有影响其他内容。它没有测出模型能力提升，也没有给镜头节奏、品牌调性或投放效果下结论。

视频领域更依赖专业经验和审美的判断，后续再专门展开。

## 3. 回到最初的问题

问渠那得清如许？为有源头活水来。
——朱熹《观书有感·其一》

名将不能只靠讲兵法来评价，Agent 也不能只看一段漂亮的回答。

沿 Agent Loop 拆职责，用 C、I、O 分别检查组件、配合与交付，再让 A 追查原因，零散的工具、记忆、多轮与人工评测就有了各自的位置。

有了这些位置，再读其他评测文章，就更容易判断某种方法解决什么问题、自己的体系还缺什么。
