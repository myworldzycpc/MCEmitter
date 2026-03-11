import os
from abc import ABC, abstractmethod
from collections import defaultdict
from enum import Enum
import copy
from typing import Optional, List, Tuple, Dict, Any, Type, Generic, TypeVar, Union, override, overload

T = TypeVar('T')
K = TypeVar('K')
TCreatable = TypeVar('TCreatable', bound="Creatable")


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
        self.is_dynamic = dynamic


class DynamicString(Argument):
    def __init__(self, *symbols: Union[str, "MacroArgument"]):
        super().__init__(dynamic=True)
        self.symbols = symbols

    def __str__(self) -> str:
        return "".join(map(str, self.symbols))


class Namespace:
    """命名空间基类"""

    def __init__(self, name: str):
        self.name = name
        Registries.NAMESPACE_REGISTRY.register_argument(self)

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"Namespace('{self.name}')"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return isinstance(other, Namespace) and self.name == other.name

    def namespaced_id(self, id: Union[str, DynamicString]):
        return NamespacedId(self, id)

    def path_namespace_id(self, path: Tuple[str | DynamicString, ...]):
        return PathNamespacedId(self, path)

    def function(self, path: tuple[str], commands: Optional[List[Command]] = None, limit_entities=None):
        return Function(self.path_namespace_id(path), commands=commands, limit_entities=limit_entities)


class NamespacedId(Argument):
    """命名空间ID处理类，负责所有命名空间相关的字符串生成和解析"""

    def __init__(self, namespace: Namespace, id: Union[str, DynamicString]):
        super().__init__()
        self.namespace = namespace
        self.id = id
        if isinstance(id, DynamicString):
            self.dynamic = True

    @classmethod
    def with_default_namespace(cls, id: str) -> "NamespacedId":
        """使用默认命名空间创建ID"""
        return cls(Config.DEFAULT_NAMESPACE, id)

    @classmethod
    def with_minecraft_namespace(cls, id: str) -> "NamespacedId":
        """使用minecraft命名空间创建ID"""
        return cls(Config.MINECRAFT_NAMESPACE, id)

    @classmethod
    def parse_full_id(cls, full_id: str) -> "NamespacedId":
        """解析完整ID为命名空间和ID部分"""
        parts = full_id.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid namespaced ID format: {full_id}. Expected format: namespace:id")
        return cls(Registries.NAMESPACE_REGISTRY.get(parts[0]), parts[1])

    def __str__(self) -> str:
        return f"{self.namespace}:{self.id}"

    def __repr__(self) -> str:
        return f"NamespacedId({self.namespace}, {self.id})"

    def __hash__(self):
        return hash(str(self))

    def __eq__(self, other):
        return isinstance(other, NamespacedId) and str(self) == str(other)


class PathNamespacedId(NamespacedId):
    """路径式命名空间ID处理类，负责路径式命名空间ID的生成和解析"""

    def __init__(self, namespace: Namespace, path: Tuple[str | DynamicString, ...]):
        super().__init__(namespace, '/'.join(map(str, path)))
        self.path = path or ()
        if any(isinstance(part, DynamicString) for part in path):
            self.is_dynamic = True

    def __add__(self, other: Tuple[Union[str, DynamicString], ...]):
        return PathNamespacedId(self.namespace, self.path + other)

    def parent(self) -> "PathNamespacedId":
        """获取父级路径式命名空间ID"""
        return PathNamespacedId(self.namespace, self.path[:-1])


# ==============================
# 注册表管理
# ==============================


class Registry(Generic[K, T]):
    """通用注册表基类"""

    def __init__(self, name: str):
        self.name = name
        self._items: Dict[K, T] = {}

    def register(self, key: K, item: T) -> None:
        if key in self._items:
            raise ValueError(f"{self.name} with key {key} already exists")
        self._items[key] = item

    def get(self, key: K) -> Optional[T]:
        return self._items[key]

    def remove(self, key: K) -> None:
        self._items.pop(key, None)

    def clear(self) -> None:
        self._items.clear()

    def get_all(self) -> List[T]:
        return list(self._items.values())


class FunctionRegistry(Registry[PathNamespacedId, 'Function']):
    """函数注册表，专门负责Function实例的管理"""

    def __init__(self):
        super().__init__("Function")
        self._anonymous_counter: defaultdict[Tuple[str, ...], int] = defaultdict(int)

    def register_function(self, function: "Function") -> None:
        """注册函数实例"""
        self.register(function.namespaced_id, function)

    def get_auto_id(self, path: Tuple[str, ...]) -> int:
        """生成匿名函数ID"""
        self._anonymous_counter[path] += 1
        return self._anonymous_counter[path] - 1

    def print_registered_functions(self) -> None:
        """打印所有注册的函数"""
        print("Registered functions:")
        for func in self.get_all():
            print(f"\n{func.namespaced_id}{" (macro)" if func.is_macro else ""}:")
            for cmd in func.commands:
                print(f"  {cmd}")

    def save_registered_functions(self) -> None:
        """保存所有注册的函数"""
        os.makedirs(Config.OUTPUT_DIR, exist_ok=True)
        for func in self.get_all():
            file_path = os.path.join(Config.OUTPUT_DIR, "data", func.namespaced_id.namespace.name, "function", "/".join(map(str, func.namespaced_id.path)) + ".mcfunction")
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


class ArgumentType(Enum):
    """参数类型枚举"""
    FUNCTION = "function"
    OBJECTIVE = "objective"
    SCORE = "score"
    INTEGER = "integer"
    FLOAT = "float"
    TEXT = "text"
    BLOCK = "block"
    ITEM = "item"
    UUID = "uuid"


class MacroArgumentRegistry(Registry[str, ArgumentType]):
    """参数注册表，专门负责命令存储args的管理"""

    def __init__(self):
        super().__init__("Macro Argument")

    def register_argument(self, macro_argument: "MacroArgument"):
        """注册参数实例"""
        self.register(macro_argument.name, macro_argument.type)


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

    def __init__(self, path: str):
        super().__init__()
        self.path = path


class StorageDataPointer(DataPointer):
    """存储数据指针"""

    def __init__(self, storage: Storage, path: str):
        super().__init__(path)
        self.storage = storage

    def __str__(self) -> str:
        return f"{self.storage} {self.path}"


class Command:
    """命令类，负责构建和表示单个命令"""

    def __init__(self, *parts: Union[str, Argument]):
        self.parts: List[Any] = []
        self.is_dynamic: bool = False
        if parts:
            for part in parts:
                self.add_part(part)

    def add_part(self, part: Any) -> "Command":
        """添加命令部分，并检测是否为动态参数"""
        self.parts.append(part)
        if isinstance(part, Argument) and part.is_dynamic:
            self.is_dynamic = True
        return self

    def __str__(self) -> str:
        """转换为命令字符串"""
        return ("$" if self.is_dynamic else "") + " ".join(str(part) for part in self.parts)

    def __repr__(self) -> str:
        return f"Command(parts={self.parts})"


class Function(Argument):
    """函数类，负责管理一组命令"""

    def __init__(self, namespaced_id: PathNamespacedId, commands: Optional[List[Command]] = None, limit_entities=None):
        if namespaced_id.is_dynamic:
            raise ValueError("Dynamic namespaced ID is not allowed for function")
        super().__init__()
        self.namespaced_id: PathNamespacedId = namespaced_id
        self.is_macro: bool = False
        self.commands: List[Command] = []
        self.limit_entities = limit_entities
        self.opened = False
        self.modified_macro_arguments: set[MacroArgument] = set()

        # 当前命令上下文参数
        self.context_stack: List[str] = []
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
        if not isinstance(command, Command):
            raise TypeError(f"Expected Command instance, got {type(command).__name__}")
        if not self.opened:
            raise ValueError("Cannot add command to closed function")
        processed_command = self._process_command_parts(command)
        self.commands.append(processed_command)

        if processed_command.is_dynamic:
            self.is_macro = True

    def add_commands(self, commands: List[Command]) -> None:
        """添加多个命令"""
        for cmd in commands:
            self.add_command(cmd)

    def create_child(self, *child_path: str, commands=None, limit_entities=None) -> "Function":
        """创建子函数"""
        for part in child_path:
            if not isinstance(part, str):
                raise TypeError(f"Expected string for child path, got {type(child_path).__name__}")
        full_path = self.namespaced_id + child_path
        return Function(namespaced_id=full_path, commands=commands, limit_entities=limit_entities)

    def __str__(self) -> str:
        """转换为函数字符串"""
        if self.is_macro:
            return f"{self.namespaced_id} with storage {Config.ARGUMENT_STORAGE}"
        else:
            return f"{self.namespaced_id}"

    def __enter__(self):
        self.opened = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.opened = False

    def _ensure_execute_context(self) -> None:
        """确保处于execute上下文中"""
        if not self.in_execute:
            self.context_stack.append("execute")
            self.in_execute = True

    def _finalize_command(self, command_parts: List[Any]) -> Command:
        """最终化命令，处理execute上下文"""
        if self.in_execute:
            self.context_stack.append("run")
            self.in_execute = False

        full_parts = self.context_stack + command_parts
        for part in full_parts:
            if isinstance(part, Argument) and part.is_dynamic:
                self.is_dynamic = True
        command = Command(*full_parts)
        command.is_dynamic = self.is_dynamic
        self.context_stack = []  # 重置上下文栈
        self.is_dynamic = False  # 重置动态参数标志
        self.add_command(command)
        return command

    def say(self, *args: Any) -> Command:
        """创建say命令"""
        return self._finalize_command(["say"] + list(args))

    def create(self, obj: TCreatable) -> 'TCreatable':
        self._finalize_command(obj.create_command().parts)
        return obj

    def set(self, target, value):
        match target:
            case Score() as score:
                if isinstance(value, int):
                    return self._finalize_command(["scoreboard", "players", "set", score, value])
                else:
                    raise TypeError(f"Expected int for score value, got {type(value).__name__}")
            case MacroArgument() as arg:
                self.modified_macro_arguments.add(arg)
                match arg.type:
                    case ArgumentType.INTEGER:
                        if isinstance(value, int):
                            return self._finalize_command(["data", "modify", "storage", StorageDataPointer(Config.ARGUMENT_STORAGE, arg.name), "set", "value", value])
                        else:
                            raise TypeError(f"Expected int for macro argument {arg.name}, got {type(value).__name__}")
                    case _:
                        raise NotImplementedError("Unsupported macro argument type for set command")
            case _:
                raise NotImplementedError("Unsupported target for set command")

    def call_function(self, function: Union["Function", PathNamespacedId]) -> Command:
        """创建调用函数的命令"""
        return self._finalize_command(["function", function])

    def sub_function(self, *path: str, commands: Optional[List[Command]] = None, limit_entities=None) -> "Function":
        """创建子函数调用命令"""
        function = self.create_child(*path, commands=commands, limit_entities=limit_entities)
        self._finalize_command(["function", function])
        return function

    def random(self, range: Range) -> Command:
        """创建random子命令"""
        return self._finalize_command(["random", range])

    def comment(self, *args: Any) -> Command:
        """创建注释命令"""
        if self.context_stack:
            raise ValueError("Cannot add comment inside execute context")
        return self._finalize_command(["#"] + list(args))

    def _add_execute_modifier(self, modifier: str, *args) -> "Function":
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

    def if_(self, *args):
        match args:
            case (Score() as score, str(operator), int(value)):
                match operator:
                    case '=':
                        return self._add_execute_modifier("if", "score", score, "matches", value)
                    case '>=':
                        return self._add_execute_modifier("if", "score", score, "matches", Range(value, None))
                    case '<=':
                        return self._add_execute_modifier("if", "score", score, "matches", Range(None, value))
                    case '>':
                        return self._add_execute_modifier("if", "score", score, "matches", Range(value + 1, None))
                    case '<':
                        return self._add_execute_modifier("if", "score", score, "matches", Range(None, value - 1))
                    case _:
                        raise ValueError(f"Invalid operator: {operator}")
            case (Score() as score, int(value)):
                return self._add_execute_modifier("if", "score", score, "matches", value)
            case (Score() as score, Range(start, end)):
                return self._add_execute_modifier("if", "score", score, "matches", Range(start, end))
            case (Score() as score, int(start), int(end)):
                return self._add_execute_modifier("if", "score", score, "matches", Range(start, end))
            case _:
                raise ValueError(f"Invalid arguments: {args}")

    def store(self, target: Score | DataPointer | MacroArgument):
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


class Selector:
    """命令选择器"""

    def __init__(self, var: SelectorVariable):
        self.var = var
        self.modifier: Optional[dict[str, Any]] = None

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

    def __str__(self) -> str:
        return f"@{self.var.value}"

    def __repr__(self) -> str:
        return f"Selector({self.var.name})"


class ScoreboardCriteria(Argument):

    def __init__(self, value: str):
        super().__init__()
        self.value = value

    @classmethod
    def dummy(cls):
        return cls("dummy")

    def __str__(self):
        return self.value


class Objective(Creatable):
    def __init__(self, objective: str, criteria: "ScoreboardCriteria" = None):
        self.name = objective
        self.criteria = criteria or ScoreboardCriteria.dummy()

        Registries.OBJECTIVE_REGISTRY.register_objective(self)

    def __getitem__(self, name: str | Selector) -> "Score":
        return Score(self, name)

    def self(self):
        return self[Selector.self()]

    def __str__(self):
        return self.name

    def create_command(self) -> "Command":
        """创建scoreboard objective add命令"""
        return Command("scoreboard", "objectives", "add", self.name, self.criteria)


class Score:
    def __init__(self, objective: Objective, name: str | Selector):
        self.objective = objective
        self.name = name

    def __str__(self):
        return f"{self.name} {self.objective}"


class Range(Argument):
    def __init__(self, start: Union[int, "MacroArgument", None], end: Union[int, "MacroArgument", None]):
        super().__init__()
        if isinstance(start, MacroArgument):
            if start.type != ArgumentType.INTEGER and start.type != ArgumentType.FLOAT:
                raise ValueError(f"Invalid macro argument type for range start: {start.type}")
            self.is_dynamic = True
        if isinstance(end, MacroArgument):
            if end.type != ArgumentType.INTEGER and end.type != ArgumentType.FLOAT:
                raise ValueError(f"Invalid macro argument type for range end: {end.type}")
            self.is_dynamic = True
        self.start = start
        self.end = end

    def __str__(self):
        return f"{self.start or ''}..{self.end or ''}"


class MacroArgument(Argument):
    """参数基类"""

    def __init__(self, name: str, type: ArgumentType):
        super().__init__(dynamic=True)
        self.name = name
        self.type = type
        Registries.MACRO_ARGUMENT_REGISTRY.register_argument(self)

    def __str__(self) -> str:
        return f"$({self.name})"

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return isinstance(other, MacroArgument) and self.name == other.name


class Config:
    DEFAULT_NAMESPACE: Namespace = Namespace("my_namespace")
    MINECRAFT_NAMESPACE: Namespace = Namespace("minecraft")
    FUNCTION_REGISTRY_CLEANUP_ON_EXIT: bool = True
    ARGUMENT_STORAGE: Storage = Storage(DEFAULT_NAMESPACE, "args")
    OUTPUT_DIR: str = "output/"


# 预定义的方块和物品类型
PREDEFINED_BLOCK_TYPES: Dict[str, BlockType] = {
    "air": BlockType.with_minecraft_namespace("air"),
    "stone": BlockType.with_minecraft_namespace("stone"),
    "dirt": BlockType.with_minecraft_namespace("dirt"),
    "grass_block": BlockType.with_minecraft_namespace("grass_block"),
}

PREDEFINED_ITEM_TYPES: Dict[str, ItemType] = {
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

    my_int = MacroArgument("my_int", ArgumentType.INTEGER)
    macro_i = MacroArgument("i", ArgumentType.INTEGER)

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
            main.set(my_int, 5)
            with main.if_(my_objective.self(), '<=', 100).sub_function("if_block") as if_block:
                if_block.say("Score is greater than or equal to 10")
                if_block.store(my_objective["test"]).random(Range(1, my_int))
                if_block.call_function(namespace.path_namespace_id(("my_function", DynamicString("function", macro_i))))

    with Namespace("other_namespace") as other_namespace:
        with other_namespace.function(("other_function",)) as other:
            other.say("Other function", Selector.self())
            with other.sub_function("other") as other:
                other.say("Other function 2", Selector.self())
                with other.sub_function("other") as other:
                    other.say("Other function 3", Selector.self())
            #     other.say("Other function 2")  # ValueError: Cannot add command to closed function
            # other.say("Other function 1")

    Registries.FUNCTION_REGISTRY.print_registered_functions()
    Registries.FUNCTION_REGISTRY.save_registered_functions()
