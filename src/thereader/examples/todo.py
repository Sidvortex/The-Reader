"""A small command-line to-do list. Sample file for The Reader."""

import json
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

SAVE_FILE = Path("todos.json")


@dataclass
class Task:
    title: str
    due: str | None = None
    done: bool = False

    def is_overdue(self) -> bool:
        if self.done or self.due is None:
            return False
        return date.fromisoformat(self.due) < date.today()


class TodoList:
    def __init__(self, path: Path = SAVE_FILE):
        self.path = path
        self.tasks: list[Task] = []
        self.load()

    def load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            print("Save file is damaged, starting fresh.")
            return
        self.tasks = [Task(**t) for t in raw]

    def save(self):
        self.path.write_text(json.dumps([asdict(t) for t in self.tasks], indent=2))

    def add(self, title: str, due: str | None = None) -> Task:
        if not title.strip():
            raise ValueError("title can't be empty")
        if due is not None:
            date.fromisoformat(due)  # raises ValueError for a bad date
        task = Task(title.strip(), due)
        self.tasks.append(task)
        self.save()
        return task

    def complete(self, number: int) -> bool:
        if not 1 <= number <= len(self.tasks):
            return False
        self.tasks[number - 1].done = True
        self.save()
        return True

    def summary(self) -> str:
        lines = []
        for i, task in enumerate(self.tasks, start=1):
            due = f" (due {task.due})" if task.due else ""
            lines.append(f"{i:>2}. [{mark_for(task)}] {task.title}{due}")
        return "\n".join(lines) or "Nothing to do."


def mark_for(task: Task) -> str:
    if task.done:
        return "x"
    if task.is_overdue():
        return "!"
    return " "


def parse(line: str) -> tuple[str, str]:
    cmd, _, rest = line.strip().partition(" ")
    return cmd.lower(), rest.strip()


def main():
    todos = TodoList()
    print("Commands: add <title> [@YYYY-MM-DD], done <n>, list, quit")
    while True:
        cmd, rest = parse(input("> "))
        match cmd:
            case "add":
                title, _, due = rest.partition("@")
                try:
                    task = todos.add(title, due.strip() or None)
                    print(f"Added: {task.title}")
                except ValueError as e:
                    print(f"Error: {e}")
            case "done":
                if rest.isdigit() and todos.complete(int(rest)):
                    print("Nice.")
                else:
                    print("No task with that number.")
            case "list":
                print(todos.summary())
            case "quit":
                break
            case _:
                print("Unknown command.")


if __name__ == "__main__":
    main()
