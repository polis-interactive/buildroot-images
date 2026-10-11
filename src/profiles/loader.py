"""Load typed profile selections. Dependency resolution is a separate step."""
from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from typing import TypeVar

from ..common import UserError
from .options import Base, Capability, Group, PROFILE_GROUPS

Option = TypeVar("Option", Base, Capability, Group)


def option(value: object, kind: type[Option], location: str) -> Option:
    if not isinstance(value, str):
        raise UserError(f"{location}: expected a {kind.__name__} string")
    try:
        return kind(value)
    except ValueError:
        raise UserError(f"{location}: unknown {kind.__name__} {value!r}; "
                        "see src/profiles/options.py for allowed values") from None


def options(value: object, kind: type[Option], location: str) -> tuple[Option, ...]:
    if not isinstance(value, list):
        raise UserError(f"{location}: expected an array")
    result = tuple(option(item, kind, f"{location}[{i}]") for i, item in enumerate(value))
    if len(set(result)) != len(result):
        raise UserError(f"{location}: duplicate selections")
    return result


@dataclass(frozen=True)
class Profile:
    name: str
    base: Base
    requires: tuple[Capability, ...]
    groups: tuple[Group, ...]


def load_profile(path: Path) -> Profile:
    """Reject invalid input before constructing the immutable typed model."""
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, ValueError) as error:
        raise UserError(f"{path}: {error}") from error
    expected = {"schema", "name", "base", "requires", "groups"}
    if missing := expected - data.keys():
        raise UserError(f"{path}: missing fields: {', '.join(sorted(missing))}")
    if unknown := data.keys() - expected:
        raise UserError(f"{path}: unknown fields: {', '.join(sorted(unknown))}")
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise UserError(f"{path}: schema must be integer 1")
    name = data["name"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name):
        raise UserError(f"{path}: name must be a lowercase identifier with optional hyphens")
    base = option(data["base"], Base, f"{path}: base")
    requires = options(data["requires"], Capability, f"{path}: requires")
    groups = options(data["groups"], Group, f"{path}: groups")
    for group in groups:
        if group not in PROFILE_GROUPS:
            raise UserError(f"{path}: {group.value} is inherited from the base; "
                            "remove it from groups")
    return Profile(name, base, requires, groups)
