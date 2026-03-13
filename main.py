from __future__ import annotations

import os
import typing
from abc import ABC, abstractmethod
from collections import defaultdict
from enum import Enum
from functools import singledispatch
from string.templatelib import Template, Interpolation
from types import TracebackType
from typing import Self, Generic, TypeVar, override, overload, Literal

import nbtlib

T = TypeVar('T')
TCovariant = TypeVar('TCovariant', covariant=True)
T2 = TypeVar('T2')
K = TypeVar('K')
TCreatable = TypeVar('TCreatable', bound="Creatable")
TBaseCovariant = TypeVar('TBaseCovariant', bound="Base[object]", covariant=True)
TBase = TypeVar('TBase', bound="Base[object]")

type CommandPartCompatible = str | Argument[object] | int | float | bool
type MaybeMacro[T] = T | MacroArgument[T]
type MaybeMacroInt = int | MaybeMacro[Int]
type CompOp = Literal['=', '>=', '<=', '>', '<']


# ==============================
# 工具类（通用功能抽离）
# ==============================

class Creatable(ABC):
    """创建接口"""

    @abstractmethod
    def create_command(self) -> "Command":
        """创建命令"""
        pass


class Argument(Generic[TCovariant], ABC):

    def __init__(self, dynamic: bool = False):
        self.is_dynamic: bool = dynamic


class Base(Argument["Base[TCovariant]"], Generic[TCovariant], ABC):
    """一切能序列化为NBT的"""
    pass


class Numeric(Base[T2], Generic[T2, T], ABC):
    def __init__(self, value: T):
        super().__init__()
        self.value: T = value

    @property
    @abstractmethod
    def suffix(self) -> str:
        pass

    @override
    def __str__(self):
        return f"{self.value}{self.suffix}"


class Int(Numeric["Int", int]):
    @property
    @override
    def suffix(self) -> str:
        return ""


class String(Base["String"]):
    def __init__(self, value: str = "", dynamic: bool = False):
        super().__init__(dynamic=dynamic)
        self.value: str = value

    @override
    def __str__(self):
        return self.value


class DynamicString(String):
    symbols: tuple[str | MacroArgument[Base[object]], ...]

    def __init__(self, *symbols: str | MacroArgument[Base[object]]):
        super().__init__(dynamic=True)
        self.symbols = symbols

    @classmethod
    def t(cls, t_string: Template):
        """从模板字符串创建动态字符串"""
        symbols: list[str | MacroArgument[Base[object]]] = []
        for part in t_string:
            if isinstance(part, Interpolation) and isinstance(part.value, MacroArgument):
                symbols.append(typing.cast(MacroArgument[Base[object]], part.value))
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


class NamespacedId(String):
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


class MacroArgumentRegistry(Registry[str, "MacroArgument[object]"]):
    """参数注册表，专门负责命令存储args的管理"""

    def __init__(self):
        super().__init__("Macro Argument")

    def register_argument(self, macro_argument: MacroArgument[object]):
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


class Path(String, nbtlib.Path):
    @override
    def __str__(self):
        return nbtlib.Path.__str__(self)


class DataHolder(ABC):
    @abstractmethod
    def parts(self) -> list[CommandPartCompatible]:
        pass

    @abstractmethod
    def full_parts(self) -> list[CommandPartCompatible]:
        pass


class Storage(NamespacedId, DataHolder):
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return [self]

    @override
    def full_parts(self) -> list[CommandPartCompatible]:
        return ["storage", self]


class DataPointer(String, Generic[TBaseCovariant], ABC):
    """数据指针基类"""
    path: Path

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    @abstractmethod
    def full_parts(self) -> list[CommandPartCompatible]:
        pass


class StorageDataPointer(DataPointer[TBaseCovariant], Generic[TBaseCovariant]):
    """存储数据指针"""
    storage: Storage

    def __init__(self, storage: Storage, path: Path):
        super().__init__(path)
        self.storage = storage

    @override
    def __str__(self) -> str:
        return f"{self.storage} {self.path}"

    @override
    def full_parts(self) -> list[CommandPartCompatible]:
        return self.storage.full_parts() + [self.path]


class CommandBase(ABC):
    def __init__(self):
        pass

    @property
    @abstractmethod
    def parts(self) -> list[CommandPartCompatible]:
        pass

    @property
    def is_dynamic(self) -> bool:
        1
        for part in self.parts:
            if isinstance(part, Argument) and part.is_dynamic:
                return True
        return False

    @override
    def __str__(self) -> str:
        """转换为命令字符串"""
        return " ".join(str(part) for part in self.parts)

    @override
    def __repr__(self) -> str:
        return f"{type(self).__name__}(parts={self.parts})"


class Command(CommandBase, ABC):
    """命令类，负责构建和表示单个命令"""

    @override
    def __str__(self) -> str:
        """转换为命令字符串"""
        return ("$" if self.is_dynamic else "") + super().__str__()


class CommentCommand(Command):
    args: tuple[CommandPartCompatible, ...]

    def __init__(self, *args: CommandPartCompatible):
        super().__init__()
        self.args = args

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return ["#"] + list(self.args)


class SayCommand(Command):
    """say命令"""
    args: tuple[CommandPartCompatible, ...]

    def __init__(self, *args: CommandPartCompatible):
        super().__init__()
        self.args = args

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return ["say"] + list(self.args)


class ExecuteSubCommand(CommandBase, ABC):
    pass


class ExecuteAsSubCommand(ExecuteSubCommand):
    """execute as子命令"""
    selector: Selector

    def __init__(self, selector: Selector):
        super().__init__()
        self.selector = selector

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return ["as", self.selector]


class ExecuteAtSubCommand(ExecuteSubCommand):
    """execute at子命令"""
    selector: Selector

    def __init__(self, selector: Selector):
        super().__init__()
        self.selector = selector

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return ["at", self.selector]


class ExecuteIfSubCommand(ExecuteSubCommand, ABC):
    @property
    @override
    @abstractmethod
    def parts(self) -> list[CommandPartCompatible]:
        return ["if"]


class ExecuteIfScoreSubCommand(ExecuteIfSubCommand, ABC):
    score: Score

    def __init__(self, score: Score):
        super().__init__()
        self.score = score

    @property
    @override
    @abstractmethod
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + ["score", self.score]


class ExecuteIfScoreMatchesSubCommand(ExecuteIfScoreSubCommand):
    range: IntRange | MaybeMacroInt

    def __init__(self, score: Score, range: IntRange | MaybeMacroInt):
        super().__init__(score)
        self.range = range

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + ["matches", self.range]


class ExecuteIfScoreOpSubCommand(ExecuteIfScoreSubCommand):
    op: CompOp
    score2: Score

    def __init__(self, score: Score, op: CompOp, score2: Score):
        super().__init__(score)
        self.op = op
        self.score2 = score2

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + [self.op, self.score2]


class ExecuteStoreSubCommand(ExecuteSubCommand):
    type: Literal["result", "success"]
    target: DataPointer[Base[object]] | Score

    def __init__(self, type: Literal["result", "success"], pointer: DataPointer[Base[object]] | Score):
        super().__init__()
        self.type = type
        self.target = pointer

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        if isinstance(self.target, DataPointer):
            return ["store", self.type] + self.target.full_parts()
        else:
            return ["store", self.type, "score", self.target]


class ExecuteCommand(Command):
    run: Command
    sub_commands: list[ExecuteSubCommand]

    def __init__(self, sub_commands: list[ExecuteSubCommand], run: Command):
        super().__init__()
        if isinstance(run, CommentCommand):
            raise ValueError("Cannot add comment inside execute context")
        self.sub_commands = sub_commands
        self.run = run

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return ["execute"] + [part for sub_command in self.sub_commands for part in sub_command.parts] + ["run"] + self.run.parts


class RandomCommand(Command, ABC):
    @property
    @override
    @abstractmethod
    def parts(self) -> list[CommandPartCompatible]:
        return ["random"]


class RandomRangeCommand(RandomCommand, ABC):
    range: IntRange
    mode: Literal["value", "roll"]
    sequence: None  # TODO: 序列参数

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + [self.mode, self.range]


class RandomValueCommand(RandomRangeCommand):
    """random value命令"""
    range: IntRange
    mode: Literal["value", "roll"] = "value"

    def __init__(self, range: IntRange):
        super().__init__()
        self.range = range


class RandomRollCommand(RandomRangeCommand):
    """random value命令"""
    range: IntRange
    mode: Literal["value", "roll"] = "roll"

    def __init__(self, range: IntRange):
        super().__init__()
        self.range = range


class ScoreboardCommand(Command, ABC):

    @property
    @override
    @abstractmethod
    def parts(self) -> list[CommandPartCompatible]:
        return ["scoreboard"]


class ScoreboardObjectivesCommand(ScoreboardCommand, ABC):
    """scoreboard objectives命令"""

    @property
    @override
    @abstractmethod
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + ["objectives"]


class ScoreboardObjectivesAddCommand(ScoreboardObjectivesCommand):
    objective: Objective

    def __init__(self, objective: Objective):
        super().__init__()
        self.objective = objective

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + ["add", self.objective, self.objective.criteria]


class ScoreboardPlayersCommand(ScoreboardCommand, ABC):
    """scoreboard players命令"""

    @property
    @override
    @abstractmethod
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + ["players"]


class ScoreboardPlayersSetCommand(ScoreboardPlayersCommand):
    score: Score
    value: MaybeMacroInt

    def __init__(self, score: Score, value: MaybeMacroInt):
        super().__init__()
        self.score = score
        self.value = value

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + ["set", self.score, self.value]


class DataCommand(Command, ABC):

    @property
    @override
    @abstractmethod
    def parts(self) -> list[CommandPartCompatible]:
        return ["data"]


class DataModifyCommand(DataCommand, Generic[TBaseCovariant], ABC):

    def __init__(self, data_pointer: DataPointer[TBaseCovariant]):
        super().__init__()
        self.data_pointer: DataPointer[TBaseCovariant] = data_pointer

    @property
    @override
    @abstractmethod
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + ["modify"] + self.data_pointer.full_parts()


class DataModifySetCommand(DataModifyCommand[TBaseCovariant], Generic[TBaseCovariant], ABC):
    """data modify ... set命令"""

    @property
    @override
    @abstractmethod
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + ["set"]


class DataModifySetValueCommand(DataModifySetCommand[TBaseCovariant], Generic[TBaseCovariant]):
    value: Base[TBaseCovariant]

    def __init__(self, data_pointer: DataPointer[TBaseCovariant], value: Base[TBaseCovariant]):
        super().__init__(data_pointer)
        self.value = value

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + ["value", self.value]


class FunctionCommand(Command):
    with_: DataPointer[Base[object]] | DataHolder | None | Literal["auto"]
    function: Function

    def __init__(self, function: Function, with_: DataPointer[Base[object]] | DataHolder | None | Literal["auto"] = "auto"):
        super().__init__()
        self.function = function
        self.with_ = with_

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        with_: DataPointer[Base[object]] | DataHolder | None = None
        if self.with_ == "auto":
            if self.function.is_macro:
                with_ = Config.ARGUMENT_STORAGE
        else:
            with_ = self.with_
        return list(["function", self.function] + (["with"] + with_.full_parts() if with_ is not None else []))


class Function(PathNamespacedId):
    """函数类，负责管理一组命令"""
    opened: bool
    limit_entities: None

    def __init__(self, namespaced_id: PathNamespacedId, commands: list[Command] | None = None, limit_entities: None = None, virtual: bool = False):
        if not virtual and namespaced_id.is_dynamic:
            raise ValueError("Dynamic namespaced ID is not allowed for function")
        super().__init__(namespaced_id.namespace, namespaced_id.path)
        self.is_macro: bool = False
        self.commands: list[Command] = []
        self.limit_entities = limit_entities  # TODO: 限制实体参数
        self.opened = False
        self.modified_macro_arguments: set[MacroArgument[object]] = set()
        self.create_from: Function | None = None
        self.virtual: bool = virtual

        # 当前命令上下文参数
        self.context_stack: list[ExecuteSubCommand] = []

        # 注册函数
        if not self.virtual:
            Registries.FUNCTION_REGISTRY.register_function(self)

        # 添加并处理命令
        if commands:
            self.add_commands(commands)

    def add_command(self, command: Command) -> None:
        """添加单个命令"""
        if not self.opened:
            raise ValueError("Cannot add command to closed function")
        self.commands.append(command)

        if command.is_dynamic:
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

    def _finalize_command(self, command: Command) -> Command:
        """最终化命令，处理execute上下文"""
        if len(self.context_stack) > 0:
            command = ExecuteCommand(self.context_stack, command)
        self.context_stack = []  # 重置上下文栈
        self.add_command(command)
        return command

    def say(self, *args: CommandPartCompatible) -> Command:
        """创建say命令"""
        return self._finalize_command(SayCommand(*args))

    def create(self, obj: TCreatable) -> 'TCreatable':
        self._finalize_command(obj.create_command())
        return obj

    @overload
    def set(self, target: Score, value: int, /) -> Command:
        ...

    @overload
    def set(self, target: MacroArgument[TBase], value: Base[TBase], /) -> Command:
        ...

    @singledispatch
    def set(self, target: Score | MacroArgument[TBase], value: int | Base[TBase]) -> Command:
        match target, value:
            case Score(), int():
                return self._finalize_command(ScoreboardPlayersSetCommand(target, value))
            case MacroArgument(), Base():
                self.modified_macro_arguments.add(target)
                return self._finalize_command(DataModifySetValueCommand(StorageDataPointer[TBase](Config.ARGUMENT_STORAGE, Path(target.name)), value))
            case _:
                raise ValueError(f"Invalid target or value: {target}, {value}")

    def call_function(self, function: Function, with_: DataPointer[Base[object]] | DataHolder | None | Literal["auto"] = "auto") -> Command:
        """创建调用函数的命令"""
        return self._finalize_command(FunctionCommand(function, with_))

    def sub_function(self, *path: str, commands: list[Command] | None = None, limit_entities: None = None) -> "Function":
        """创建子函数调用命令"""
        function = self.create_child(*path, commands=commands, limit_entities=limit_entities)
        function.create_from = self
        return function

    def random_value(self, range: IntRange) -> Command:
        """创建random子命令"""
        return self._finalize_command(RandomValueCommand(range))

    def comment(self, *args: CommandPartCompatible) -> Command:
        """创建注释命令"""
        return self._finalize_command(CommentCommand(*args))

    def _add_execute_sub_command(self, sub_command: ExecuteSubCommand) -> "Function":
        """通用的execute修饰符添加方法（复用代码）"""
        self.context_stack.append(sub_command)
        return self

    def as_(self, selector: Selector) -> "Function":
        """添加as修饰符"""
        return self._add_execute_sub_command(ExecuteAsSubCommand(selector))

    def at(self, selector: Selector) -> "Function":
        """添加at修饰符"""
        return self._add_execute_sub_command(ExecuteAtSubCommand(selector))

    def as_and_at(self, selector: Selector) -> "Function":
        """同时添加as和at修饰符（替代原ast方法，更易理解）"""
        return self.as_(selector).at(Selector.self())

    @overload
    def if_(self, score: Score, operator: CompOp, value: MaybeMacroInt, /) -> "Function":
        ...

    @overload
    def if_(self, score: Score, value: MaybeMacroInt, /) -> "Function":
        ...

    @overload
    def if_(self, score: Score, range: Range, /) -> "Function":
        ...

    @overload
    def if_(self, score: Score, start: MaybeMacroInt, end: MaybeMacroInt, /) -> "Function":
        ...

    def if_(self, *args: object) -> "Function":
        match args:
            case (Score() as score, str(operator), MacroArgument() as value):
                value = typing.cast(MacroArgument[Int], value)
                match operator:
                    case '=':
                        return self._add_execute_sub_command(ExecuteIfScoreMatchesSubCommand(score, value))
                    case '>=':
                        return self._add_execute_sub_command(ExecuteIfScoreMatchesSubCommand(score, IntRange(value, None)))
                    case '<=':
                        return self._add_execute_sub_command(ExecuteIfScoreMatchesSubCommand(score, IntRange(None, value)))
                    case _:
                        raise ValueError(f"Invalid operator: {operator}")
            case (Score() as score, str(operator), int(value)):
                match operator:
                    case '=':
                        return self._add_execute_sub_command(ExecuteIfScoreMatchesSubCommand(score, value))
                    case '>=':
                        return self._add_execute_sub_command(ExecuteIfScoreMatchesSubCommand(score, IntRange(value, None)))
                    case '<=':
                        return self._add_execute_sub_command(ExecuteIfScoreMatchesSubCommand(score, IntRange(None, value)))
                    case '>':
                        return self._add_execute_sub_command(ExecuteIfScoreMatchesSubCommand(score, IntRange(value + 1, None)))
                    case '<':
                        return self._add_execute_sub_command(ExecuteIfScoreMatchesSubCommand(score, IntRange(None, value - 1)))
                    case _:
                        raise ValueError(f"Invalid operator: {operator}")
            case (Score() as score, int(value)):
                return self._add_execute_sub_command(ExecuteIfScoreMatchesSubCommand(score, value))
            case (Score() as score, IntRange() as range):
                return self._add_execute_sub_command(ExecuteIfScoreMatchesSubCommand(score, range))
            case (Score() as score, int(start), int(end)):
                return self._add_execute_sub_command(ExecuteIfScoreMatchesSubCommand(score, IntRange(start, end)))
            case _:
                raise ValueError(f"Invalid arguments: {args}")

    def store(self, type: Literal["result", "success"], target: Score | DataPointer[Base[object]] | MacroArgument[Base[object]]):
        """添加store子命令"""
        match target:
            case Score():
                return self._add_execute_sub_command(ExecuteStoreSubCommand(type, target))
            case StorageDataPointer():
                return self._add_execute_sub_command(ExecuteStoreSubCommand(type, target))
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


class Selector(String):
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


class ScoreboardCriteria(String):
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


class Objective(String, Creatable):
    criteria: ScoreboardCriteria
    name: str

    def __init__(self, objective: str, criteria: ScoreboardCriteria | None = None):
        super().__init__()
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
        return ScoreboardObjectivesAddCommand(self)


class Score(String):
    name: str | Selector
    objective: Objective

    def __init__(self, objective: Objective, name: str | Selector):
        super().__init__()
        self.objective = objective
        self.name = name

    @override
    def __str__(self):
        return f"{self.name} {self.objective}"


class Range(Argument["Range"], ABC):
    end: object
    start: object

    def __init__(self, start: object, end: object):
        is_dynamic = False
        if isinstance(start, MacroArgument) or isinstance(end, MacroArgument):
            is_dynamic = True
        super().__init__(dynamic=is_dynamic)
        self.start = start
        self.end = end

    @override
    def __str__(self):
        return f"{self.start or ''}..{self.end or ''}"


class IntRange(Range):
    def __init__(self, start: int | MacroArgument[Int] | None, end: int | MacroArgument[Int] | None):
        super().__init__(start, end)


class FloatRange(Range):
    def __init__(self, start: int | float | MacroArgument[Numeric[object, object]], end: int | float | MacroArgument[Numeric[object, object]]):
        super().__init__(start, end)


class MacroArgument(Base[TCovariant], Generic[TCovariant], ABC):
    """宏参数基类"""
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
                if_block.store("result", my_objective["test"]).random_value(IntRange(1, my_int))
                if_block.call_function(Function(namespace.path_namespace_id(("my_function", DynamicString.t(t"function{macro_i}"))), virtual=True))

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
