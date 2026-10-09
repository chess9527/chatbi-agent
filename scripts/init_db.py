"""生成示例电商数据库（确定性随机，可重复构建），供 ChatBI 演示与评估使用。"""
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbi.config import get_settings  # noqa: E402

CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都"]
CATEGORIES = ["数码", "服饰", "食品", "家居", "美妆"]
PRODUCT_NAMES = {
    "数码": ["无线耳机", "智能手环", "蓝牙音箱", "充电宝", "机械键盘"],
    "服饰": ["连帽卫衣", "牛仔裤", "运动鞋", "冲锋衣", "棒球帽"],
    "食品": ["坚果礼盒", "咖啡豆", "巧克力", "茶叶", "麦片"],
    "家居": ["加湿器", "台灯", "收纳箱", "香薰机", "四件套"],
    "美妆": ["面膜", "口红", "精华液", "防晒霜", "香水"],
}
STATUSES = ["completed", "completed", "completed", "shipped", "pending", "cancelled"]


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS users;

        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            city TEXT NOT NULL,
            phone TEXT NOT NULL,
            register_date TEXT NOT NULL
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER NOT NULL
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            order_date TEXT NOT NULL,
            status TEXT NOT NULL,
            total_amount REAL NOT NULL
        );
        CREATE TABLE order_items (
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES orders(id),
            product_id INTEGER NOT NULL REFERENCES products(id),
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL
        );
        CREATE INDEX idx_orders_user ON orders(user_id);
        CREATE INDEX idx_items_order ON order_items(order_id);
    """)


def seed_data(conn: sqlite3.Connection) -> None:
    rng = random.Random(42)
    start = date(2025, 1, 1)

    users = [(i, f"用户{i:03d}", rng.choice(CITIES), f"138{rng.randint(10 ** 7, 10 ** 8 - 1):08d}",
              (start + timedelta(days=rng.randint(0, 300))).isoformat()) for i in range(1, 61)]
    products = []
    pid = 1
    # 库存取唯一值，保证 Top-K 类问题结果确定性（无并列）
    stocks = rng.sample(range(0, 500), 25)
    for category in CATEGORIES:
        for name in PRODUCT_NAMES[category]:
            products.append((pid, name, category, round(rng.uniform(19, 2999), 2), stocks[pid - 1]))
            pid += 1

    orders, items = [], []
    oid, iid = 1, 1
    for _ in range(400):
        user = rng.choice(users)
        order_date = (start + timedelta(days=rng.randint(0, 364))).isoformat()
        status = rng.choice(STATUSES)
        order, amount = [], 0.0
        for _ in range(rng.randint(1, 4)):
            product = rng.choice(products)
            qty = rng.randint(1, 3)
            amount += qty * product[3]
            order.append((iid, oid, product[0], qty, product[3]))
            iid += 1
        orders.append((oid, user[0], order_date, status, round(amount, 2)))
        items.extend(order)
        oid += 1

    conn.executemany("INSERT INTO users VALUES (?,?,?,?,?)", users)
    conn.executemany("INSERT INTO products VALUES (?,?,?,?,?)", products)
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
    conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)


def init_db() -> Path:
    path = Path(get_settings().db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        create_tables(conn)
        seed_data(conn)
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    db = init_db()
    print(f"示例电商数据库已生成: {db}")
