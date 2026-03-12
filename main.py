from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Sequence
from enum import Enum
from string.templatelib import Template, Interpolation
from types import TracebackType
from typing import Self, Generic, TypeVar, override, overload, Literal

from nbtlib import Base, Int, Numeric

T = TypeVar('T')
K = TypeVar('K')
TCreatable = TypeVar('TCreatable', bound="Creatable")
TArgument = TypeVar('TArgument', bound="Argument")
TBase = TypeVar('TBase', bound=Base)
TBaseCovariant = TypeVar('TBaseCovariant', bound=Base, covariant=True)

type CommandPartCompatible = str | Argument | int | float | bool


# ==============================
# 工具类（通用功能抽离）
# ==============================
class Creatable(ABC):
    """创建接口"""

    @abstractmethod
    def create_command(self) -> "Command":
        """创建命令"""
        pass


class Argument(ABC):

    def __init__(self, dynamic: bool = False):
        self.is_dynamic: bool = dynamic


class StringArgument(Argument):
    pass


class DynamicString(StringArgument):
    symbols: tuple[str | MacroArgument[Base], ...]

    def __init__(self, *symbols: str | MacroArgument[Base]):
        super().__init__(dynamic=True)
        self.symbols = symbols

    @classmethod
    def t(cls, t_string: Template):
        """从模板字符串创建动态字符串"""
        symbols: list[str | MacroArgument[Base]] = []
        for part in t_string:
            if isinstance(part, Interpolation) and isinstance(part.value, MacroArgument):
                symbols.append(part.value)
            elif isinstance(part, str):
                symbols.append(part)
            else:
                raise ValueError(f"Invalid part type in template string: {type(part).__name__}")
        return DynamicString(*symbols)

    @override
    def __str__(self) -> str:
        return "".join(map(str, self.symbols))


class Namespace:
    """命名空间基类"""
    name: str

    def __init__(self, name: str):
        self.name = name
        Registries.NAMESPACE_REGISTRY.register_argument(self)

    @override
    def __str__(self) -> str:
        return self.name

    @override
    def __repr__(self) -> str:
        return f"Namespace('{self.name}')"

    def __enter__(self):
        return self

    def __exit__(self, exc_type: type, exc_val: BaseException, exc_tb: TracebackType):
        pass

    @override
    def __hash__(self):
        return hash(self.name)

    @override
    def __eq__(self, other: object):
        return isinstance(other, Namespace) and self.name == other.name

    def namespaced_id(self, id: str | DynamicString):
        return NamespacedId(self, id)

    def path_namespace_id(self, path: tuple[str | DynamicString, ...]):
        return PathNamespacedId(self, path)

    def function(self, path: tuple[str], commands: list[Command] | None = None, limit_entities: None = None):
        return Function(self.path_namespace_id(path), commands=commands, limit_entities=limit_entities)


class NamespacedId(StringArgument):
    """命名空间ID处理类，负责所有命名空间相关的字符串生成和解析"""
    dynamic: bool
    id: str | DynamicString
    namespace: Namespace

    def __init__(self, namespace: Namespace, id: str | DynamicString):
        super().__init__()
        self.namespace = namespace
        self.id = id
        if isinstance(id, DynamicString):
            self.dynamic = True

    @classmethod
    def with_default_namespace(cls, id: str) -> Self:
        """使用默认命名空间创建ID"""
        return cls(Config.DEFAULT_NAMESPACE, id)

    @classmethod
    def with_minecraft_namespace(cls, id: str) -> Self:
        """使用minecraft命名空间创建ID"""
        return cls(Config.MINECRAFT_NAMESPACE, id)

    @classmethod
    def parse_full_id(cls, full_id: str) -> "NamespacedId":
        """解析完整ID为命名空间和ID部分"""
        parts = full_id.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid namespaced ID format: {full_id}. Expected format: namespace:id")
        return cls(Registries.NAMESPACE_REGISTRY.get(parts[0]), parts[1])

    @override
    def __str__(self) -> str:
        return f"{self.namespace}:{self.id}"

    @override
    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.namespace}, {self.id})"

    @override
    def __hash__(self):
        return hash(str(self))

    @override
    def __eq__(self, other: object):
        return isinstance(other, NamespacedId) and str(self) == str(other)


class PathNamespacedId(NamespacedId):
    """路径式命名空间ID处理类，负责路径式命名空间ID的生成和解析"""
    is_dynamic: bool
    path: tuple[str | DynamicString, ...] | tuple[()]

    def __init__(self, namespace: Namespace, path: tuple[str | DynamicString, ...]):
        super().__init__(namespace, '/'.join(map(str, path)))
        self.path = path or ()
        if any(isinstance(part, DynamicString) for part in path):
            self.is_dynamic = True

    def __add__(self, other: tuple[str | DynamicString, ...]):
        return PathNamespacedId(self.namespace, self.path + other)

    def parent(self) -> "PathNamespacedId":
        """获取父级路径式命名空间ID"""
        return PathNamespacedId(self.namespace, self.path[:-1])


# ==============================
# 注册表管理
# ==============================


class Registry(Generic[K, T]):
    """通用注册表基类"""
    name: str

    def __init__(self, name: str):
        self.name = name
        self._items: dict[K, T] = {}

    def register(self, key: K, item: T) -> None:
        if key in self._items:
            raise ValueError(f"{self.name} with key {key} already exists")
        self._items[key] = item

    def get(self, key: K) -> T:
        return self._items[key]

    def remove(self, key: K) -> None:
        self._items.pop(key, None)

    def clear(self) -> None:
        self._items.clear()

    def get_all(self) -> list[T]:
        return list(self._items.values())


class FunctionRegistry(Registry[PathNamespacedId, 'Function']):
    """函数注册表，专门负责Function实例的管理"""

    def __init__(self):
        super().__init__("Function")
        self._anonymous_counter: defaultdict[tuple[str, ...], int] = defaultdict(int)

    def register_function(self, function: Function) -> None:
        """注册函数实例"""
        self.register(function, function)

    def get_auto_id(self, path: tuple[str, ...]) -> int:
        """生成匿名函数ID"""
        self._anonymous_counter[path] += 1
        return self._anonymous_counter[path] - 1

    def print_registered_functions(self) -> None:
        """打印所有注册的函数"""
        print("Registered functions:")
        for func in self.get_all():
            print(f"\n{func}{" (macro)" if func.is_macro else ""}:")
            for cmd in func.commands:
                print(f"  {cmd}")

    def save_registered_functions(self) -> None:
        """保存所有注册的函数"""
        os.makedirs(Config.OUTPUT_DIR, exist_ok=True)
        for func in self.get_all():
            file_path = os.path.join(Config.OUTPUT_DIR, "data", func.namespace.name, "function", "/".join(map(str, func.path)) + ".mcfunction")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                for cmd in func.commands:
                    f.write(str(cmd) + "\n")


class ObjectiveRegistry(Registry[str, 'Objective']):
    """计分板目标注册表，专门负责Objective实例的管理"""

    def __init__(self):
        super().__init__("Objective")

    def register_objective(self, objective: "Objective") -> None:
        """注册计分板目标实例"""
        self.register(objective.name, objective)


class MacroArgumentRegistry(Registry[str, "MacroArgument[Base]"]):
    """参数注册表，专门负责命令存储args的管理"""

    def __init__(self):
        super().__init__("Macro Argument")

    def register_argument(self, macro_argument: MacroArgument[Base]):
        """注册参数实例"""
        self.register(macro_argument.name, macro_argument)


class NamespaceRegistry(Registry[str, Namespace]):
    """命名空间注册表，专门负责命名空间的管理"""

    def __init__(self):
        super().__init__("Namespace")

    def register_argument(self, namespace: "Namespace"):
        """注册参数实例"""
        self.register(namespace.name, namespace)


class Registries:
    FUNCTION_REGISTRY: FunctionRegistry = FunctionRegistry()
    OBJECTIVE_REGISTRY: ObjectiveRegistry = ObjectiveRegistry()
    MACRO_ARGUMENT_REGISTRY: MacroArgumentRegistry = MacroArgumentRegistry()
    NAMESPACE_REGISTRY: NamespaceRegistry = NamespaceRegistry()


# ==============================
# 核心业务类
# ==============================
class BlockType(NamespacedId):
    """方块类型，基于命名空间ID"""
    pass


class ItemType(NamespacedId):
    """物品类型，基于命名空间ID"""
    pass


class Storage(NamespacedId):
    pass


class DataPointer(Argument):
    """数据指针基类"""
    path: str

    def __init__(self, path: str):
        super().__init__()
        self.path = path


class StorageDataPointer(DataPointer):
    """存储数据指针"""
    storage: Storage

    def __init__(self, storage: Storage, path: str):
        super().__init__(path)
        self.storage = storage

    @override
    def __str__(self) -> str:
        return f"{self.storage} {self.path}"


class Command:
    """命令类，负责构建和表示单个命令"""

    def __init__(self, *parts: CommandPartCompatible):
        self.parts: list[CommandPartCompatible] = []
        self.is_dynamic: bool = False
        if parts:
            for part in parts:
                self.add_part(part)

    def add_part(self, part: CommandPartCompatible) -> "Command":
        """添加命令部分，并检测是否为动态参数"""
        self.parts.append(part)
        if isinstance(part, Argument) and part.is_dynamic:
            self.is_dynamic = True
        return self

    @override
    def __str__(self) -> str:
        """转换为命令字符串"""
        return ("$" if self.is_dynamic else "") + " ".join(str(part) for part in self.parts)

    @override
    def __repr__(self) -> str:
        return f"Command(parts={self.parts})"


class Function(PathNamespacedId):
    """函数类，负责管理一组命令"""
    opened: bool
    limit_entities: None

    def __init__(self, namespaced_id: PathNamespacedId, commands: list[Command] | None = None, limit_entities: None = None):
        if namespaced_id.is_dynamic:
            raise ValueError("Dynamic namespaced ID is not allowed for function")
        super().__init__(namespaced_id.namespace, namespaced_id.path)
        self.is_macro: bool = False
        self.commands: list[Command] = []
        self.limit_entities = limit_entities  # TODO: 限制实体参数
        self.opened = False
        self.modified_macro_arguments: set[MacroArgument[Base]] = set()
        self.create_from: Function | None = None

        # 当前命令上下文参数
        self.context_stack: list[CommandPartCompatible] = []
        self.in_execute: bool = False
        self.is_dynamic: bool = False

        # 注册函数
        Registries.FUNCTION_REGISTRY.register_function(self)

        # 添加并处理命令
        if commands:
            self.add_commands(commands)

    def _process_command_parts(self, command: Command) -> Command:
        """处理命令中的子函数参数"""
        # processed_parts = []
        # for part in command.parts:
        #     processed_parts.append(part)
        # command.parts = processed_parts
        return command

    def add_command(self, command: Command) -> None:
        """添加单个命令"""
        if not self.opened:
            raise ValueError("Cannot add command to closed function")
        processed_command = self._process_command_parts(command)
        self.commands.append(processed_command)

        if processed_command.is_dynamic:
            self.is_macro = True

    def add_commands(self, commands: list[Command]) -> None:
        """添加多个命令"""
        for cmd in commands:
            self.add_command(cmd)

    def create_child(self, *child_path: str, commands: list[Command] | None = None, limit_entities: None = None) -> "Function":
        """创建子函数"""
        full_path = self + child_path
        return Function(namespaced_id=full_path, commands=commands, limit_entities=limit_entities)

    def __enter__(self):
        self.opened = True
        return self

    def __exit__(self, exc_type: type, exc_val: BaseException, exc_tb: TracebackType):
        self.opened = False
        if self.create_from:
            self.create_from.call_function(self)

    def _ensure_execute_context(self) -> None:
        """确保处于execute上下文中"""
        if not self.in_execute:
            self.context_stack.append("execute")
            self.in_execute = True

    def _finalize_command(self, command_parts: Sequence[CommandPartCompatible]) -> Command:
        """最终化命令，处理execute上下文"""
        if self.in_execute:
            self.context_stack.append("run")
            self.in_execute = False
        self.context_stack.extend(command_parts)
        full_parts = self.context_stack
        for part in full_parts:
            if isinstance(part, Argument) and part.is_dynamic:
                self.is_dynamic = True
        command = Command(*full_parts)
        command.is_dynamic = self.is_dynamic
        self.context_stack = []  # 重置上下文栈
        self.is_dynamic = False  # 重置动态参数标志
        self.add_command(command)
        return command

    def say(self, *args: CommandPartCompatible) -> Command:
        """创建say命令"""
        return self._finalize_command(["say"] + list(args))

    def create(self, obj: TCreatable) -> 'TCreatable':
        self._finalize_command(obj.create_command().parts)
        return obj

    @overload
    def set(self, target: Score, value: int) -> Command:
        ...

    @overload
    def set(self, target: MacroArgument[TBase], value: TBase) -> Command:
        ...

    def set(self, target: Score | MacroArgument[TBase], value: int | TBase):
        match target:
            case Score() as score:
                if isinstance(value, int):
                    return self._finalize_command(["scoreboard", "players", "set", score, value])
                else:
                    raise TypeError(f"Expected int for score value, got {type(value).__name__}")
            case MacroArgument() as arg:
                self.modified_macro_arguments.add(arg)
                if isinstance(value, int):
                    return self._finalize_command(["data", "modify", "storage", StorageDataPointer(Config.ARGUMENT_STORAGE, arg.name), "set", "value", value])
                else:
                    raise TypeError(f"Expected int for macro argument {arg.name}, got {type(value).__name__}")

    def call_function(self, function: Function | PathNamespacedId, macro: bool | None = None) -> Command:
        """创建调用函数的命令"""
        with_storage = False
        if macro is not None:
            with_storage = macro
        else:
            if isinstance(function, Function):
                with_storage = function.is_macro
        return self._finalize_command(["function", function] + (["with", "storage", Config.ARGUMENT_STORAGE] if with_storage else []))

    def sub_function(self, *path: str, commands: list[Command] | None = None, limit_entities: None = None) -> "Function":
        """创建子函数调用命令"""
        function = self.create_child(*path, commands=commands, limit_entities=limit_entities)
        function.create_from = self
        return function

    def random(self, range: IntRange) -> Command:
        """创建random子命令"""
        return self._finalize_command(["random", range])

    def comment(self, *args: CommandPartCompatible) -> Command:
        """创建注释命令"""
        if self.context_stack:
            raise ValueError("Cannot add comment inside execute context")
        return self._finalize_command(["#"] + list(args))

    def _add_execute_modifier(self, modifier: str, *args: CommandPartCompatible) -> "Function":
        """通用的execute修饰符添加方法（复用代码）"""
        self._ensure_execute_context()
        self.context_stack.extend([modifier, *args])
        return self

    def as_(self, selector: Selector) -> "Function":
        """添加as修饰符"""
        return self._add_execute_modifier("as", selector)

    def at(self, selector: Selector) -> "Function":
        """添加at修饰符"""
        return self._add_execute_modifier("at", selector)

    def as_and_at(self, selector: Selector) -> "Function":
        """同时添加as和at修饰符（替代原ast方法，更易理解）"""
        return self.as_(selector).at(Selector.self())

    @overload
    def if_(self, score: Score, operator: Literal['=', '>=', '<=', '>', '<'], value: int, /) -> "Function":
        ...

    @overload
    def if_(self, score: Score, value: int, /) -> "Function":
        ...

    @overload
    def if_(self, score: Score, range: Range, /) -> "Function":
        ...

    @overload
    def if_(self, score: Score, start: int, end: int, /) -> "Function":
        ...

    def if_(self, *args: object):
        match args:
            case (Score() as score, str(operator), int(value)):
                match operator:
                    case '=':
                        return self._add_execute_modifier("if", "score", score, "matches", value)
                    case '>=':
                        return self._add_execute_modifier("if", "score", score, "matches", IntRange(value, None))
                    case '<=':
                        return self._add_execute_modifier("if", "score", score, "matches", IntRange(None, value))
                    case '>':
                        return self._add_execute_modifier("if", "score", score, "matches", IntRange(value + 1, None))
                    case '<':
                        return self._add_execute_modifier("if", "score", score, "matches", IntRange(None, value - 1))
                    case _:
                        raise ValueError(f"Invalid operator: {operator}")
            case (Score() as score, int(value)):
                return self._add_execute_modifier("if", "score", score, "matches", value)
            case (Score() as score, IntRange() as range):
                return self._add_execute_modifier("if", "score", score, "matches", range)
            case (Score() as score, int(start), int(end)):
                return self._add_execute_modifier("if", "score", score, "matches", IntRange(start, end))
            case _:
                raise ValueError(f"Invalid arguments: {args}")

    def store(self, target: Score | DataPointer | MacroArgument[Base]):
        """添加store子命令"""
        match target:
            case Score() as score:
                return self._add_execute_modifier("store", "score", score)
            case StorageDataPointer() as pointer:
                return self._add_execute_modifier("store", "storage", pointer)
            case _:
                raise NotImplementedError("Unsupported target for store command")


class SelectorVariable(Enum):
    """选择器变量枚举"""
    SELF = "s"
    ALL = "e"
    PLAYERS = "a"
    NEAREST = "n"
    NEAREST_PLAYER = "p"
    RANDOM = "r"


class Selector(Argument):
    """命令选择器"""

    def __init__(self, var: SelectorVariable):
        super().__init__()
        self.var: SelectorVariable = var
        self.modifier: dict[str, object] | None = None

    @classmethod
    def self(cls) -> "Selector":
        return cls(SelectorVariable.SELF)

    @classmethod
    def all(cls) -> "Selector":
        return cls(SelectorVariable.ALL)

    @classmethod
    def nearest_player(cls) -> "Selector":
        return cls(SelectorVariable.NEAREST_PLAYER)

    def distance(self, distance: "Range"):
        self.modifier = {"distance": distance}

    @override
    def __str__(self) -> str:
        return f"@{self.var.value}"

    @override
    def __repr__(self) -> str:
        return f"Selector({self.var.name})"


class ScoreboardCriteria(Argument):
    value: str

    def __init__(self, value: str):
        super().__init__()
        self.value = value

    @classmethod
    def dummy(cls):
        return cls("dummy")

    @override
    def __str__(self):
        return self.value


class Objective(Creatable):
    criteria: ScoreboardCriteria
    name: str

    def __init__(self, objective: str, criteria: ScoreboardCriteria | None = None):
        self.name = objective
        self.criteria = criteria or ScoreboardCriteria.dummy()

        Registries.OBJECTIVE_REGISTRY.register_objective(self)

    def __getitem__(self, name: str | Selector) -> "Score":
        return Score(self, name)

    def self(self):
        return self[Selector.self()]

    @override
    def __str__(self):
        return self.name

    @override
    def create_command(self) -> "Command":
        """创建scoreboard objective add命令"""
        return Command("scoreboard", "objectives", "add", self.name, self.criteria)


class Score(Argument):
    name: str | Selector
    objective: Objective

    def __init__(self, objective: Objective, name: str | Selector):
        super().__init__()
        self.objective = objective
        self.name = name

    @override
    def __str__(self):
        return f"{self.name} {self.objective}"


class Range(Argument, ABC):
    end: object
    start: object

    def __init__(self, start: object, end: object):
        super().__init__()
        self.start = start
        self.end = end

    @override
    def __str__(self):
        return f"{self.start or ''}..{self.end or ''}"


class IntRange(Range):
    def __init__(self, start: int | MacroArgument[Int] | None, end: int | MacroArgument[Int] | None):
        super().__init__(start, end)


class FloatRange(Range):
    def __init__(self, start: int | float | MacroArgument[Numeric], end: int | float | MacroArgument[Numeric]):
        super().__init__(start, end)


class MacroArgument(Argument, Generic[TBaseCovariant], ABC):
    """参数基类"""
    name: str

    def __init__(self, name: str):
        super().__init__(dynamic=True)
        self.name = name
        Registries.MACRO_ARGUMENT_REGISTRY.register_argument(self)

    @override
    def __str__(self) -> str:
        return f"$({self.name})"

    @override
    def __hash__(self):
        return hash(self.name)

    @override
    def __eq__(self, other: object):
        return isinstance(other, MacroArgument) and self.name == other.name


class Config:
    DEFAULT_NAMESPACE: Namespace = Namespace("my_namespace")
    MINECRAFT_NAMESPACE: Namespace = Namespace("minecraft")
    FUNCTION_REGISTRY_CLEANUP_ON_EXIT: bool = True
    ARGUMENT_STORAGE: Storage = Storage(DEFAULT_NAMESPACE, "args")
    OUTPUT_DIR: str = "output/"


# 预定义的方块和物品类型
PREDEFINED_BLOCK_TYPES: dict[str, BlockType] = {
    "air": BlockType.with_minecraft_namespace("air"),
    "stone": BlockType.with_minecraft_namespace("stone"),
    "dirt": BlockType.with_minecraft_namespace("dirt"),
    "grass_block": BlockType.with_minecraft_namespace("grass_block"),
}

PREDEFINED_ITEM_TYPES: dict[str, ItemType] = {
    "stone_sword": ItemType.with_minecraft_namespace("stone_sword"),
    "stone_pickaxe": ItemType.with_minecraft_namespace("stone_pickaxe"),
    "stone_axe": ItemType.with_minecraft_namespace("stone_axe"),
}

# ==============================
# 示例使用
# ==============================
if __name__ == "__main__":
    # 创建上下文

    my_objective = Objective("my_scoreboard")

    my_int: MacroArgument[Int] = MacroArgument[Int]("my_int")
    macro_i = MacroArgument[Int]("i")

    # 创建函数
    with Config.DEFAULT_NAMESPACE as namespace:
        with namespace.function(("my_function",)) as main:
            main.say("Hello, world!", Selector.self())
            main.create(my_objective)
            with main.as_and_at(Selector.all()).sub_function("child_function") as child:
                child.say("Child function", Selector.self())
                child.say("2")
                child.comment("This is a comment")
                child.say("3")
                child.set(my_objective.self(), 10)
            main.set(my_int, Int(5))
            with main.if_(my_objective.self(), '<=', 100).sub_function("if_block") as if_block:
                if_block.say("Score is greater than or equal to 10")
                if_block.store(my_objective["test"]).random(IntRange(1, my_int))
                if_block.call_function(namespace.path_namespace_id(("my_function", DynamicString.t(t"function{macro_i}"))))

        with namespace.function(("energy_tide",)) as energy_tide:
            energy_tide.say(1)


    with Namespace("other_namespace") as other_namespace:
        with other_namespace.function(("other_function",)) as other:
            other.say("Other function", Selector.self())
            with other.sub_function("other") as other1:
                other1.say("Other function 2", Selector.self())
                with other1.sub_function("other") as other2:
                    other2.say("Other function 3", Selector.self(), my_int)

    Registries.FUNCTION_REGISTRY.print_registered_functions()
    Registries.FUNCTION_REGISTRY.save_registered_functions()
