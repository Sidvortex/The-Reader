"""Tiny inventory system used to demo The Reader."""

import json
from pathlib import Path
from dataclasses import dataclass, field

LOW_STOCK = 5


@dataclass
class Item:
    sku: str
    name: str
    qty: int = 0
    tags: list[str] = field(default_factory=list)


class Inventory:
    """Keeps items in memory and persists them to a JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self.items: dict[str, Item] = {}
        self.load()

    def load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except json.JSONDecodeError as e:
            print(f"corrupt inventory file: {e}")
            return
        for raw in data:
            self.items[raw["sku"]] = Item(**raw)

    def save(self):
        self.path.write_text(json.dumps([vars(i) for i in self.items.values()], indent=2))

    def restock(self, sku: str, amount: int) -> int:
        if amount <= 0:
            raise ValueError("amount must be positive")
        item = self.items.get(sku)
        if item is None:
            raise KeyError(sku)
        item.qty += amount
        self.save()
        return item.qty

    def sell(self, sku: str, amount: int = 1) -> bool:
        item = self.items.get(sku)
        if item is None or item.qty < amount:
            return False
        item.qty -= amount
        if item.qty <= LOW_STOCK:
            notify_low_stock(item)
        self.save()
        return True

    def report(self) -> list[str]:
        lines = []
        for item in sorted(self.items.values(), key=lambda i: i.qty):
            if item.qty == 0:
                status = "OUT"
            elif item.qty <= LOW_STOCK:
                status = "LOW"
            else:
                continue
            lines.append(f"{status:>4}  {item.sku}  {item.name} ({item.qty})")
        return lines


def notify_low_stock(item: Item) -> None:
    print(f"[alert] {item.name} is down to {item.qty}")


def parse_command(line: str) -> tuple[str, list[str]]:
    parts = line.strip().split()
    return (parts[0].lower(), parts[1:]) if parts else ("", [])


def main():
    inv = Inventory(Path("inventory.json"))
    while True:
        cmd, args = parse_command(input("> "))
        match cmd:
            case "sell":
                ok = inv.sell(args[0], int(args[1]) if len(args) > 1 else 1)
                print("sold" if ok else "not enough stock")
            case "restock":
                try:
                    print("now", inv.restock(args[0], int(args[1])))
                except (KeyError, ValueError) as e:
                    print("error:", e)
            case "report":
                for line in inv.report():
                    print(line)
            case "quit":
                break
            case _:
                print("commands: sell, restock, report, quit")


if __name__ == "__main__":
    main()
