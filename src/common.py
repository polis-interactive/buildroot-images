"""Shared paths, process execution, and explicit selection."""
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys


class UserError(Exception):
    """An actionable CLI error."""


@dataclass(frozen=True)
class Context:
    repo: Path
    buildroot: Path
    downloads: Path
    snapshots: Path

    @property
    def boards(self) -> list[str]:
        return sorted(p.name.removesuffix("_defconfig")
                      for p in (self.repo / "configs").glob("*_defconfig"))

    def output(self, board: str, override: Path | None = None) -> Path:
        return (override or self.repo / "output" / board).resolve()

    def require_board(self, board: str) -> None:
        if board not in self.boards:
            raise UserError(f"Unknown board {board!r}. Use 'boards' to list choices.")


def run(args, *, capture=False, cwd=None, env=None, input=None):
    return subprocess.run([str(a) for a in args], check=True, text=True,
                          capture_output=capture, cwd=cwd, env=env, input=input)


def choose(options: list, label: str):
    """Always list choices; never select one implicitly, including in CI."""
    for index, option in enumerate(options, 1):
        print(f"{index:2}. {option}")
    if not options:
        print(f"No {label.lower()} options available.")
        return None
    if not sys.stdin.isatty():
        print(f"Specify --{label.lower()} to select explicitly.")
        return None
    answer = input(f"{label} number (blank cancels): ").strip()
    if not answer:
        return None
    if not answer.isdecimal() or not 1 <= int(answer) <= len(options):
        raise UserError(f"Invalid {label.lower()} selection.")
    return options[int(answer) - 1]


def read_config(path: Path) -> dict[str, str]:
    return {key: value.strip('"')
            for line in path.read_text().splitlines()
            if line.startswith("BR2_") and "=" in line
            for key, value in [line.split("=", 1)]}
