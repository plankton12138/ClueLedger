# hastings.bible — Story Bible 文件制存储层

> 状态：P1 已实现（`8fcc79a`）。代码：[src/hastings/bible/](../../src/hastings/bible/)，测试：[tests/test_bible_store.py](../../tests/test_bible_store.py)。

## 职责

把 Story Bible（角色卡等设定实体）以**文件制工作区**形式持久化：
`stories/<slug>/bible/characters/*.yaml`，与 coding agent 操作代码库同构——
可 inspect、可 git、可工具化（DESIGN.md §2.2）。

## 公开接口

```python
# models.py
class Character(BaseModel):
    name: str            # 必填，1–64 字符
    identity: str = ""   # 身份
    motivation: str = "" # 动机
    secret: str = ""     # 秘密
    relationships: list[str] = []
    notes: str = ""

# store.py
@dataclass(frozen=True)
class StoryWorkspace:
    root: Path; slug: str
    # 属性：story_dir / bible_dir / characters_dir / chapters_dir
    def ensure_dirs() -> None

def save_character(ws, char) -> Path        # 落盘（同名覆盖）
def load_character(ws, name) -> Character | None
def list_characters(ws) -> list[Character]  # 按文件名排序；空库返回 []
def character_to_yaml(char) -> str          # 序列化（bible_query 展示用）
def validate_entity_name(name) -> str       # 不合法抛 InvalidEntityNameError
```

## 关键决策

| 决策 | 理由 |
|---|---|
| Pydantic 做 schema 校验，YAML 仅作序列化层 | pydantic 已是依赖；校验与格式解耦，P2 换存储不动模型 |
| 全部 `yaml` 调用收敛在 store.py | mypy strict 下隔离 `safe_load` 返回的 `Any`；业务代码只面对模型 |
| `allow_unicode=True, sort_keys=False` | 中文不转义、字段序稳定——Bible 文件要人类可读可 diff |
| 实体名 slug 校验（禁点号/斜杠/反斜杠，首字符限中英文数字） | **安全红线**：实体名是唯一写盘路径来源，杜绝路径穿越 |
| P1 仅 `Character` 一种实体，字段为 DESIGN.md §2.2 最小子集 | 弧光状态机等字段 P2 再加，模型层预留扩展但不提前实现 |

## 约束与红线

- 业务代码（工具层等）**禁止**直接 `import yaml`；
- 新增实体类型时：模型进 `models.py`，读写函数进 `store.py`，测试先行；
- 文件名即实体名，改名=删旧写新（P1 不提供 rename）。

## 演化方向（P2）

- 新增 Clue / Foreshadow / Method / Timeline 账本与各自状态机（`planned → planted → …`）；
- 状态迁移合法性校验进工具层，存储层保持" dumb but safe "。
