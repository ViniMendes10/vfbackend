import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get('DATABASE_URL', '')

# Render uses postgres:// but psycopg2 needs postgresql://
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)


def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        name TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS customers (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        city TEXT,
        phone TEXT,
        birthday TEXT,
        notes TEXT,
        vip INTEGER DEFAULT 0,
        total_spent REAL DEFAULT 0,
        total_orders INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW()
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT,
        brand TEXT,
        description TEXT,
        cost_price REAL DEFAULT 0,
        sale_price REAL DEFAULT 0,
        stock INTEGER DEFAULT 0,
        min_stock INTEGER DEFAULT 5,
        status TEXT DEFAULT 'ativo',
        created_at TIMESTAMP DEFAULT NOW()
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS supplier_orders (
        id SERIAL PRIMARY KEY,
        order_date TEXT NOT NULL,
        supplier TEXT NOT NULL,
        freight REAL DEFAULT 0,
        notes TEXT,
        total_products REAL DEFAULT 0,
        total_order REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW()
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS supplier_order_items (
        id SERIAL PRIMARY KEY,
        order_id INTEGER NOT NULL REFERENCES supplier_orders(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(id),
        quantity INTEGER NOT NULL,
        unit_cost REAL NOT NULL,
        total_cost REAL NOT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS sales (
        id SERIAL PRIMARY KEY,
        sale_date TEXT NOT NULL,
        product_id INTEGER NOT NULL REFERENCES products(id),
        quantity INTEGER NOT NULL,
        customer_id INTEGER REFERENCES customers(id),
        customer_name TEXT,
        customer_city TEXT,
        customer_phone TEXT,
        unit_price REAL NOT NULL,
        total_price REAL NOT NULL,
        cost_price REAL DEFAULT 0,
        profit REAL DEFAULT 0,
        payment_method TEXT DEFAULT 'pix',
        payment_status TEXT DEFAULT 'pago',
        notes TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS expenses (
        id SERIAL PRIMARY KEY,
        expense_date TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        amount REAL NOT NULL,
        notes TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS goals (
        id SERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        goal_type TEXT NOT NULL,
        target_value REAL NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        notes TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    )''')

    # Default admin
from werkzeug.security import generate_password_hash
c.execute("DELETE FROM users WHERE username='admin'")
c.execute("INSERT INTO users (username, password, name) VALUES (%s, %s, %s)",
          ('admin', generate_password_hash('admin123'), 'Administrador'))

    conn.commit()
    conn.close()
    print("✅ Database initialized")
