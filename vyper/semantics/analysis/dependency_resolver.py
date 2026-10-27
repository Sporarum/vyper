from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from vyper import ast as vy_ast
from vyper.semantics.analysis.common import VyperNodeVisitorBase
from vyper.utils import OrderedSet


class _MemberExtractor:
    def __init__(self):
        self.processing: set[vy_ast.Module] = set()
        self.seen: dict[vy_ast.Module, ModuleMembers] = dict()

    def process_r(self, module_ast: vy_ast.Module):
        assert module_ast not in self.processing

        if module_ast not in self.seen:
            self.processing.add(module_ast)

            members: dict[str, vy_ast.Decl | ModuleMembers] = {}
            for node in module_ast.body:
                if isinstance(node, vy_ast.VariableDecl):
                    members[node.target.id] = node
                elif isinstance(node, vy_ast.Decl):
                    assert not isinstance(node, vy_ast.VariableDecl)  # help mypy
                    members[node.name] = node
                elif isinstance(node, (vy_ast.Import, vy_ast.ImportFrom)):
                    for info in node._metadata.get("import_infos", []):
                        if isinstance(info.parsed, vy_ast.Module):
                            members[info.alias] = self.process_r(info.parsed)

            self.processing.remove(module_ast)
            self.seen[module_ast] = ModuleMembers(members)

        return self.seen[module_ast]


def extract_members(root_module_ast: vy_ast.Module) -> dict[vy_ast.Module, ModuleMembers]:
    tmp = _MemberExtractor()
    tmp.process_r(root_module_ast)

    return tmp.seen


@dataclass
class ModuleMembers:
    members: dict[str, vy_ast.Decl | ModuleMembers]


def compute_dependencies(
    module_ast: vy_ast.Module, module_members: ModuleMembers
) -> dict[vy_ast.Decl, OrderedSet[vy_ast.Decl]]:
    dependency_resolver = _DependencyResolver(module_members)
    return {
        node: dependency_resolver.visit(node)
        for node in module_ast.body
        if isinstance(node, vy_ast.Decl)
    }


def _resolve_attribute(
    node: vy_ast.VyperNode, module_members: ModuleMembers
) -> Optional[vy_ast.Decl | ModuleMembers]:
    match node:
        case vy_ast.Name(id=id):
            return module_members.members.get(id)
        case vy_ast.Attribute(attr=attr, value=value):
            new_module_info = _resolve_attribute(value, module_members)
            if isinstance(new_module_info, ModuleMembers):
                return new_module_info.members.get(attr)
            else:
                return None
        case _:
            return None


# TODO: This has a lot of pass-through cases that should really be asserts, but validation
# happens later
class _DependencyResolver(VyperNodeVisitorBase[OrderedSet[vy_ast.Decl]]):
    """
    Note: Only analyzes externally visible dependencies, not for example references made only
          in the body of a function.
    """

    scope_name = "module"

    def __init__(self, module_members: ModuleMembers):
        self.module_members = module_members

    def visit_FunctionDef(self, node: vy_ast.FunctionDef) -> OrderedSet[vy_ast.Decl]:
        "Functions depend on their parameter and return types, and parameter default values"

        arg_type_deps: OrderedSet[vy_ast.Decl] = OrderedSet(
            dep for arg in node.args.args for dep in self.visit(arg.annotation)
        )

        default_deps: OrderedSet[vy_ast.Decl] = OrderedSet(
            dep for expr in node.args.defaults for dep in self.visit(expr)
        )

        return arg_type_deps | self.visit(node.returns) | default_deps

    def visit_VariableDecl(self, node: vy_ast.VariableDecl) -> OrderedSet[vy_ast.Decl]:
        # TODO: Do we want the value here ?
        return self.visit(node.annotation) | self.visit(node.value)

    def visit_StructDef(self, node: vy_ast.StructDef) -> OrderedSet[vy_ast.Decl]:
        return OrderedSet(
            dep
            for member in node.get_children(vy_ast.AnnAssign)
            for dep in self.visit(member.annotation)
        )

    def visit_FlagDef(self, node: vy_ast.FlagDef) -> OrderedSet[vy_ast.Decl]:
        return OrderedSet()

    def visit_InterfaceDef(self, node: vy_ast.InterfaceDef) -> OrderedSet[vy_ast.Decl]:
        return OrderedSet(
            dep for stub in node.get_children(vy_ast.FunctionDef) for dep in self.visit(stub)
        )

    def visit_EventDef(self, node: vy_ast.EventDef) -> OrderedSet[vy_ast.Decl]:
        return OrderedSet(
            dep
            for member in node.get_children(vy_ast.AnnAssign)
            for dep in self.visit(member.annotation)
        )

    def visit_ErrorDef(self, node: vy_ast.ErrorDef) -> OrderedSet[vy_ast.Decl]:
        return OrderedSet(
            dep
            for member in node.get_children(vy_ast.AnnAssign)
            for dep in self.visit(member.annotation)
        )

    def visit_Name(self, node: vy_ast.Name) -> OrderedSet[vy_ast.Decl]:
        member = self.module_members.members.get(node.id)

        # bit of a hack, functions need to be accessed through `self`
        # so this is actually a miss-lookup of a function named like a built-in
        # for example `def int256()`
        # however (contract) functions cannot be called in constants, so this is fine
        if isinstance(member, vy_ast.FunctionDef):
            return OrderedSet()

        if isinstance(member, vy_ast.Decl):
            return OrderedSet([member])
        else:
            return OrderedSet()

    def visit_Attribute(self, node: vy_ast.Attribute) -> OrderedSet[vy_ast.Decl]:
        resolved = _resolve_attribute(node, self.module_members)
        if isinstance(resolved, vy_ast.Decl):
            return OrderedSet([resolved])
        # Fall back to visiting the base so that e.g. `F.A` (member access on
        # a local FlagDef) records a dep on `F`.
        # Note: self.x is not handled by this function, but this is fine since only immutables are
        # present in signatures
        return self.visit(node.value)

    def visit_VyperNode(self, node: vy_ast.VyperNode) -> OrderedSet[vy_ast.Decl]:
        return OrderedSet(dep for child in node.get_children() for dep in self.visit(child))

    def visit_NoneType(self, node: None) -> OrderedSet[vy_ast.Decl]:
        return OrderedSet()
