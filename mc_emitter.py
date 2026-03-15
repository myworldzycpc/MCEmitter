from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections import defaultdict
from enum import Enum
from string.templatelib import Template, Interpolation
from types import TracebackType
from typing import Self, Generic, override, overload, Literal, TypeVar

T = TypeVar('T')
TCovariant = TypeVar('TCovariant', covariant=True)
T2 = TypeVar('T2')
K = TypeVar('K')
TCreatable = TypeVar('TCreatable', bound="Creatable")
TBaseCovariant = TypeVar('TBaseCovariant', bound="NbtType", covariant=True)
TNumericCovariant = TypeVar('TNumericCovariant', bound="NbtNumericType", covariant=True)
TBase = TypeVar('TBase', bound="NbtType")

type CommandPartCompatible = str | Argument | int | float | bool
type IntLike = int | IntType
type FloatLike = float | FloatType
type NumericLike = IntLike | FloatLike
type StringLike = str | StringType
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


class Serializable(ABC):
    @abstractmethod
    def to_nbt(self) -> NbtBase:
        pass


class Argument(ABC):

    @property
    @abstractmethod
    def is_dynamic(self) -> bool:
        pass


class IntType(Argument, ABC): pass


class FloatType(Argument, ABC): pass


class StringType(Argument, ABC): pass


class NbtType(Argument, ABC):
    type_name: str


class NbtNumericType(NbtType, ABC): pass


class NbtNumericIntegerType(NbtNumericType, ABC): pass


class NbtIntType(NbtNumericIntegerType, ABC):
    type_name: str = "int"


class NbtStringType(NbtType, ABC):
    type_name: str = "string"


class NbtCompoundType(NbtType, ABC):
    type_name: str = "compound"


class NbtBase(NbtType, ABC):
    """NBT基类"""
    pass


class NbtNumeric(NbtBase, NbtNumericType, Generic[T], ABC):
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

    @property
    @override
    def is_dynamic(self) -> bool:
        return False


class NbtInt(NbtNumeric[int], NbtIntType):

    @property
    @override
    def suffix(self) -> str:
        return ""


class NbtString(NbtBase, NbtStringType):
    def __init__(self, value: str | DynamicString = ""):
        self.value: str | DynamicString = value

    @override
    def __str__(self):
        return repr(self.value)

    @property
    @override
    def is_dynamic(self) -> bool:
        if isinstance(self.value, DynamicString):
            if self.value.is_dynamic:
                return True

        return False


class DynamicString(StringType):
    symbols: tuple[str | MacroArgument, ...]

    def __init__(self, *symbols: str | MacroArgument):
        self.symbols = symbols

    @classmethod
    def t(cls, t_string: Template):
        """从模板字符串创建动态字符串"""
        symbols: list[str | MacroArgument] = []
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

    @property
    @override
    def is_dynamic(self) -> bool:
        if any(isinstance(symbol, MacroArgument) for symbol in self.symbols):
            return True
        return False


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


class NamespacedId(Argument):
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

    @property
    @override
    def is_dynamic(self) -> bool:
        if isinstance(self.id, DynamicString) and self.id.is_dynamic:
            return True

        return False


class PathNamespacedId(NamespacedId):
    """路径式命名空间ID处理类，负责路径式命名空间ID的生成和解析"""

    path: tuple[str | DynamicString, ...] | tuple[()]

    def __init__(self, namespace: Namespace, path: tuple[str | DynamicString, ...]):
        super().__init__(namespace, '/'.join(map(str, path)))
        self.path = path or ()

    def __add__(self, other: tuple[str | DynamicString, ...]):
        return PathNamespacedId(self.namespace, self.path + other)

    def parent(self) -> "PathNamespacedId":
        """获取父级路径式命名空间ID"""
        return PathNamespacedId(self.namespace, self.path[:-1])

    @property
    @override
    def is_dynamic(self) -> bool:
        if any(isinstance(part, DynamicString) for part in self.path):
            return True
        return False


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
        self.register(str(objective.name), objective)


class MacroArgumentRegistry(Registry[str, "MacroArgument"]):
    """参数注册表，专门负责命令存储args的管理"""

    def __init__(self):
        super().__init__("Macro Argument")

    def register_argument(self, macro_argument: MacroArgument):
        """注册参数实例"""
        self.register(macro_argument.name, macro_argument)


class NamespaceRegistry(Registry[str, Namespace]):
    """命名空间注册表，专门负责命名空间的管理"""

    def __init__(self):
        super().__init__("Namespace")

    def register_argument(self, namespace: Namespace):
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


class PathPart(ABC):
    @property
    @abstractmethod
    def is_need_dot(self) -> bool:
        pass

    @property
    @abstractmethod
    def is_dynamic(self) -> bool:
        pass


class PathKey(PathPart):
    key: StringLike

    def __init__(self, key: StringLike):
        self.key = key

    @property
    @override
    def is_need_dot(self) -> bool:
        return True

    @property
    @override
    def is_dynamic(self) -> bool:
        if isinstance(self.key, Argument):
            return self.key.is_dynamic
        return False

    @override
    def __str__(self):
        if len(str(self.key)) == 0:
            return '""'
        if set(str(self.key)).issubset(Config.IDENTIFIER_ALLOWED):
            return str(self.key)
        return repr(str(self.key))


class PathIndex(PathPart):
    index: None | IntLike | NbtCompoundType

    def __init__(self, index: None | IntLike | NbtCompoundType = None):
        self.index = index

    @property
    @override
    def is_dynamic(self) -> bool:
        if isinstance(self.index, Argument):
            return self.index.is_dynamic
        return False

    @property
    @override
    def is_need_dot(self) -> bool:
        return False

    @override
    def __str__(self):
        if self.index is None:
            return "[]"
        return f"[{self.index}]"


class Path(Argument):
    parts: tuple[PathPart, ...]

    def __init__(self, *parts: PathPart):
        self.parts = parts

    @property
    @override
    def is_dynamic(self) -> bool:
        return any(part.is_dynamic for part in self.parts)

    @override
    def __str__(self):
        result = ""
        for i, part in enumerate(self.parts):
            if part.is_need_dot and i > 0:
                result += "."
            result += str(part)
        return result

    def __getitem__(self, item: str | int | slice | None) -> Path:
        if isinstance(item, int):
            return Path(*self.parts, PathIndex(item))
        elif isinstance(item, str):
            return Path(*self.parts, PathKey(item))
        elif isinstance(item, slice):
            if item.start is None and item.stop is None and item.step is None:
                return Path(*self.parts, PathIndex())
            else:
                raise ValueError("Invalid slice for path")
        elif item is None:
            return Path(*self.parts, PathIndex())
        raise ValueError("Invalid index for path")


class DataHolder(Argument, ABC):
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


class DataPointer(Argument, Generic[TBaseCovariant], ABC):
    """数据指针基类"""
    path: Path
    data_type: type[TBaseCovariant]

    def __init__(self, path: Path, data_type: type[TBaseCovariant]):
        super().__init__()
        self.path = path
        self.data_type = data_type

    @abstractmethod
    def full_parts(self) -> list[CommandPartCompatible]:
        pass


class StorageDataPointer(DataPointer[TBaseCovariant], Generic[TBaseCovariant]):
    """存储数据指针"""

    storage: Storage

    def __init__(self, storage: Storage, path: Path, data_type: type[TBaseCovariant]):
        super().__init__(path, data_type)
        self.storage = storage

    @override
    def __str__(self) -> str:
        return f"{self.storage} {self.path}"

    @override
    def full_parts(self) -> list[CommandPartCompatible]:
        return self.storage.full_parts() + [self.path]

    @property
    @override
    def is_dynamic(self) -> bool:
        return self.storage.is_dynamic or self.path.is_dynamic


class CommandBase(ABC):
    def __init__(self):
        pass

    @property
    @abstractmethod
    def parts(self) -> list[CommandPartCompatible]:
        pass

    @property
    def is_dynamic(self) -> bool:
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
    range: IntRange | IntLike

    def __init__(self, score: Score, range: IntRange | IntLike):
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


class ExecuteStoreSubCommand(ExecuteSubCommand, ABC):
    type: Literal["result", "success"]

    def __init__(self, type: Literal["result", "success"]):
        super().__init__()
        self.type = type

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return ["store", self.type]


class ExecuteStoreScoreSubCommand(ExecuteStoreSubCommand):

    def __init__(self, type: Literal["result", "success"], score: Score):
        super().__init__(type)
        self.score: Score = score

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + ["score", self.score]


class ExecuteStoreDataSubCommand(ExecuteStoreSubCommand, Generic[TNumericCovariant]):
    data_pointer: DataPointer[TNumericCovariant]
    data_type: type[TNumericCovariant]
    scale: NumericLike

    def __init__(self, type: Literal["result", "success"], data_pointer: DataPointer[TNumericCovariant], scale: NumericLike = 1):
        super().__init__(type)
        self.data_pointer = data_pointer
        self.data_type = data_pointer.data_type
        self.scale = scale

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + self.data_pointer.full_parts() + [self.data_type.type_name, self.scale]


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
    value: IntLike

    def __init__(self, score: Score, value: IntLike):
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
    value: TBaseCovariant

    def __init__(self, data_pointer: DataPointer[TBaseCovariant], value: TBaseCovariant):
        super().__init__(data_pointer)
        self.value = value

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        return super().parts + ["value", self.value]


class FunctionCommand(Command):
    with_: DataPointer[NbtCompoundType] | DataHolder | None | Literal["auto"]
    function: Function

    def __init__(self, function: Function, with_: DataPointer[NbtCompoundType] | DataHolder | None | Literal["auto"] = "auto"):
        super().__init__()
        self.function = function
        self.with_ = with_

    @property
    @override
    def parts(self) -> list[CommandPartCompatible]:
        with_: DataPointer[NbtCompoundType] | DataHolder | None = None
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
        self.modified_macro_arguments: set[MacroArgument] = set()
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
    def set(self, target: MacroArgument, value: NbtType, /) -> Command:
        ...

    def set(self, target: Score | MacroArgument, value: int | NbtType) -> Command:
        match target, value:
            case Score(), int():
                return self._finalize_command(ScoreboardPlayersSetCommand(target, value))
            case MacroArgument(), NbtType():
                self.modified_macro_arguments.add(target)
                return self._finalize_command(DataModifySetValueCommand(StorageDataPointer(Config.ARGUMENT_STORAGE, Path()[target.name], target.expected_type), value))
            case _:
                raise ValueError(f"Invalid target or value: {target}, {value}")

    def call_function(self, function: Function, with_: DataPointer[NbtCompoundType] | DataHolder | None | Literal["auto"] = "auto") -> Command:
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
    def if_(self, score: Score, operator: CompOp, value: IntLike, /) -> "Function":
        ...

    @overload
    def if_(self, score: Score, value: IntLike, /) -> "Function":
        ...

    @overload
    def if_(self, score: Score, range: Range, /) -> "Function":
        ...

    @overload
    def if_(self, score: Score, start: IntLike, end: IntLike, /) -> "Function":
        ...

    def if_(self, *args: object) -> "Function":
        match args:
            case (Score() as score, str(operator), IntMacroArgument() as value):
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

    @overload
    def store(self, type: Literal["result", "success"], target: Score) -> Function:
        ...

    @overload
    def store(self, type: Literal["result", "success"], target: DataPointer[NbtNumericType] | MacroArgument, scale: IntLike = 1) -> Function:
        ...

    def store(self, type: Literal["result", "success"], target: Score | DataPointer[NbtNumericType] | MacroArgument, scale: IntLike = 1):
        """添加store子命令"""
        match target:
            case Score():
                return self._add_execute_sub_command(ExecuteStoreScoreSubCommand(type, target))
            case StorageDataPointer():
                return self._add_execute_sub_command(ExecuteStoreDataSubCommand(type, target))
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


class SelectorArgument(ABC):
    """选择器参数基类"""

    @property
    @abstractmethod
    def is_dynamic(self) -> bool:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass


class SelectorDistanceArgument(SelectorArgument):
    """选择器距离参数"""

    distance: Range

    def __init__(self, distance: Range):
        self.distance = distance

    @property
    @override
    def is_dynamic(self) -> bool:
        return self.distance.is_dynamic

    @property
    @override
    def name(self) -> str:
        return "distance"

    @override
    def __str__(self) -> str:
        return str(self.distance)


class Selector(Argument):
    """命令选择器"""

    def __init__(self, var: SelectorVariable):
        super().__init__()
        self.var: SelectorVariable = var
        self.arguments: list[SelectorArgument] = []

    @classmethod
    def self(cls) -> Selector:
        return cls(SelectorVariable.SELF)

    @classmethod
    def all(cls) -> Selector:
        return cls(SelectorVariable.ALL)

    @classmethod
    def nearest_player(cls) -> Selector:
        return cls(SelectorVariable.NEAREST_PLAYER)

    def distance(self, distance: Range):
        self.arguments.append(SelectorDistanceArgument(distance))

    @override
    def __str__(self) -> str:
        if len(self.arguments) == 0:
            return f"@{self.var.value}"
        return f"@{self.var.value}[{','.join(f"{arg.name}={str(arg)}" for arg in self.arguments)}]"

    @override
    def __repr__(self) -> str:
        return f"Selector({self.var.name})"

    @property
    @override
    def is_dynamic(self) -> bool:
        for arg in self.arguments:
            if arg.is_dynamic:
                return True
        return False


class ScoreboardCriteria(Argument, ABC):
    pass


class ScoreboardSingleCriteria(ScoreboardCriteria):
    value: str

    def __init__(self, value: str):
        super().__init__()
        self.value = value

    @override
    def __str__(self):
        return self.value

    @property
    @override
    def is_dynamic(self) -> bool:
        return False


class Objective(Argument, Creatable):
    criteria: ScoreboardCriteria
    name: StringLike

    def __init__(self, objective: StringLike, criteria: ScoreboardCriteria | None = None):
        super().__init__()
        self.name = objective
        self.criteria = criteria or PREDEFINED_SCOREBOARD_CRITERIA["dummy"]
        Registries.OBJECTIVE_REGISTRY.register_objective(self)

    def __getitem__(self, name: str | Selector) -> "Score":
        return Score(self, name)

    def self(self):
        return self[Selector.self()]

    @override
    def __str__(self):
        return str(self.name)

    @override
    def create_command(self) -> "Command":
        """创建scoreboard objective add命令"""
        return ScoreboardObjectivesAddCommand(self)

    @property
    @override
    def is_dynamic(self) -> bool:
        if isinstance(self.name, StringType):
            if self.name.is_dynamic:
                return True
        return False


class Score(Argument):
    name: StringLike | Selector
    objective: Objective

    def __init__(self, objective: Objective, name: str | Selector):
        super().__init__()
        self.objective = objective
        self.name = name

    @override
    def __str__(self):
        return f"{self.name} {self.objective}"

    @property
    @override
    def is_dynamic(self) -> bool:
        if isinstance(self.name, Argument):
            if self.name.is_dynamic:
                return True
        return False


class Range(Argument, ABC):
    end: object
    start: object

    def __init__(self, start: object, end: object):
        self.start = start
        self.end = end

    @override
    def __str__(self):
        return f"{self.start or ''}..{self.end or ''}"

    @property
    @override
    def is_dynamic(self) -> bool:
        if isinstance(self.start, MacroArgument) or isinstance(self.end, MacroArgument):
            return True
        return False


class IntRange(Range):
    def __init__(self, start: IntLike | None, end: IntLike | None):
        super().__init__(start, end)


class FloatRange(Range):
    def __init__(self, start: NumericLike | None, end: NumericLike | None):
        super().__init__(start, end)


class MacroArgument(Argument, ABC):
    """宏参数Mixin"""
    name: str

    def __init__(self, name: str):
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

    @property
    @override
    def is_dynamic(self) -> bool:
        return True

    @property
    @abstractmethod
    def expected_type(self) -> type[NbtType]:
        pass

    @property
    def data_pointer(self) -> DataPointer[NbtType]:
        return StorageDataPointer(Config.ARGUMENT_STORAGE, Path()[self.name], self.expected_type)


class IntMacroArgument(IntType, NbtIntType, MacroArgument):
    """整数宏参数"""

    @property
    @override
    def expected_type(self) -> type[NbtType]:
        return NbtIntType


class Config:
    DEFAULT_NAMESPACE: Namespace = Namespace("my_namespace")
    MINECRAFT_NAMESPACE: Namespace = Namespace("minecraft")
    FUNCTION_REGISTRY_CLEANUP_ON_EXIT: bool = True
    ARGUMENT_STORAGE: Storage = Storage(DEFAULT_NAMESPACE, "args")
    OUTPUT_DIR: str = "../output/"
    IDENTIFIER_ALLOWED: set[str] = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


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

PREDEFINED_SCOREBOARD_CRITERIA: dict[str, ScoreboardCriteria] = {
    "dummy": ScoreboardSingleCriteria("dummy"),
}

