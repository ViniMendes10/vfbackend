import os, json
from datetime import datetime, date
from flask import Flask, request, jsonify, session, g
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_conn, init_db
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'vf-importados-gold-secret-2024')

# CORS — allow Vercel frontend
CORS(app, supports_credentials=True,
     origins=os.environ.get('FRONTEND_URL', '*').split(','))


# ── Auth helpers ──────────────────────────────────────────────────────────────
def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Não autorizado'}), 401
        return f(*args, **kwargs)
    return decorated

def ok(data=None, **kwargs):
    payload = kwargs
    if data is not None:
        payload['data'] = data
    return jsonify(payload)

def rows_to_list(rows):
    return [dict(r) for r in rows] if rows else []

def safe_float(v, d=0.0):
    try: return float(v)
    except: return d

def safe_int(v, d=0):
    try: return int(v)
    except: return d


# ── Auth routes ───────────────────────────────────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
def login():
    body = request.get_json() or {}
    username = body.get('username', '').strip()
    password = body.get('password', '').strip()
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=%s", (username,))
    user = c.fetchone(); conn.close()
    if user and check_password_hash(user['password'], password):
        session['user_id']   = user['id']
        session['username']  = user['username']
        session['user_name'] = user['name']
        return ok(user={'id': user['id'], 'username': user['username'], 'name': user['name']})
    return jsonify({'error': 'Usuário ou senha inválidos'}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return ok(message='Logout realizado')

@app.route('/api/auth/me', methods=['GET'])
def me():
    if 'user_id' not in session:
        return jsonify({'error': 'Não autorizado'}), 401
    return ok(user={'id': session['user_id'], 'username': session['username'], 'name': session['user_name']})


# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route('/api/dashboard', methods=['GET'])
@require_auth
def dashboard():
    conn = get_conn(); c = conn.cursor()
    today     = date.today().isoformat()
    month_start = date.today().replace(day=1).isoformat()

    def scalar(sql, params=()):
        c.execute(sql, params); r = c.fetchone()
        return list(r.values())[0] if r else 0

    data = {
        'total_invested':   scalar("SELECT COALESCE(SUM(total_order),0) FROM supplier_orders"),
        'total_freight':    scalar("SELECT COALESCE(SUM(freight),0) FROM supplier_orders"),
        'total_stock':      scalar("SELECT COALESCE(SUM(stock),0) FROM products WHERE status='ativo'"),
        'total_gross':      scalar("SELECT COALESCE(SUM(total_price),0) FROM sales WHERE payment_status='pago'"),
        'total_profit':     scalar("SELECT COALESCE(SUM(profit),0) FROM sales WHERE payment_status='pago'"),
        'total_sales_qty':  scalar("SELECT COUNT(*) FROM sales"),
        'pending_value':    scalar("SELECT COALESCE(SUM(total_price),0) FROM sales WHERE payment_status='pendente'"),
        'total_expenses':   scalar("SELECT COALESCE(SUM(amount),0) FROM expenses"),
        'today_sales':      scalar("SELECT COALESCE(SUM(total_price),0) FROM sales WHERE payment_status='pago' AND sale_date=%s", (today,)),
        'month_sales':      scalar("SELECT COALESCE(SUM(total_price),0) FROM sales WHERE payment_status='pago' AND sale_date>=%s", (month_start,)),
        'month_profit':     scalar("SELECT COALESCE(SUM(profit),0) FROM sales WHERE payment_status='pago' AND sale_date>=%s", (month_start,)),
        'month_qty':        scalar("SELECT COUNT(*) FROM sales WHERE sale_date>=%s", (month_start,)),
    }

    # Goal
    c.execute("SELECT * FROM goals WHERE goal_type='vendas' AND period_start<=%s AND period_end>=%s ORDER BY id DESC LIMIT 1", (today, today))
    goal = c.fetchone()
    data['goal'] = dict(goal) if goal else None
    data['goal_pct'] = min(int((data['month_sales'] / goal['target_value']) * 100), 150) if goal and goal['target_value'] > 0 else 0

    # Chart last 6 months
    import calendar as cal
    chart_labels, chart_data = [], []
    t = date.today()
    for i in range(5, -1, -1):
        m = t.month - i
        y = t.year
        while m <= 0: m += 12; y -= 1
        ms = f"{y}-{m:02d}-01"
        me = f"{y}-{m:02d}-{cal.monthrange(y,m)[1]:02d}"
        val = scalar("SELECT COALESCE(SUM(total_price),0) FROM sales WHERE payment_status='pago' AND sale_date BETWEEN %s AND %s", (ms, me))
        chart_labels.append(f"{m:02d}/{y}")
        chart_data.append(round(float(val), 2))
    data['chart_labels'] = chart_labels
    data['chart_data']   = chart_data

    # Top products
    c.execute('''SELECT p.name, SUM(s.quantity) qty, SUM(s.total_price) revenue
        FROM sales s JOIN products p ON s.product_id=p.id
        GROUP BY p.id, p.name ORDER BY qty DESC LIMIT 5''')
    data['top_products'] = rows_to_list(c.fetchall())

    # Recent sales
    c.execute('''SELECT s.*, p.name product_name FROM sales s
        JOIN products p ON s.product_id=p.id ORDER BY s.created_at DESC LIMIT 8''')
    data['recent_sales'] = rows_to_list(c.fetchall())

    # Recent orders
    c.execute("SELECT * FROM supplier_orders ORDER BY created_at DESC LIMIT 5")
    data['recent_orders'] = rows_to_list(c.fetchall())

    # Low stock
    c.execute("SELECT * FROM products WHERE stock<=min_stock AND status='ativo' ORDER BY stock LIMIT 6")
    data['low_stock'] = rows_to_list(c.fetchall())

    # Birthdays today
    c.execute("SELECT * FROM customers WHERE birthday IS NOT NULL AND TO_CHAR(birthday::date,'MM-DD')=TO_CHAR(NOW(),'MM-DD')")
    data['birthday_alerts'] = rows_to_list(c.fetchall())

    conn.close()
    return ok(data)


# ── Products ──────────────────────────────────────────────────────────────────
@app.route('/api/products', methods=['GET'])
@require_auth
def products_list():
    conn = get_conn(); c = conn.cursor()
    q   = request.args.get('q', '')
    cat = request.args.get('cat', '')
    if q:
        c.execute('''SELECT * FROM products WHERE name ILIKE %s OR brand ILIKE %s
            OR CAST(id AS TEXT)=%s OR category ILIKE %s ORDER BY name''',
            (f'%{q}%', f'%{q}%', q, f'%{q}%'))
    elif cat:
        c.execute("SELECT * FROM products WHERE category=%s ORDER BY name", (cat,))
    else:
        c.execute("SELECT * FROM products ORDER BY name")
    rows = rows_to_list(c.fetchall())
    c.execute("SELECT DISTINCT category FROM products WHERE category IS NOT NULL ORDER BY category")
    cats = [r['category'] for r in c.fetchall()]
    conn.close()
    return ok({'products': rows, 'categories': cats})

@app.route('/api/products/<int:pid>', methods=['GET'])
@require_auth
def product_get(pid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM products WHERE id=%s", (pid,))
    row = c.fetchone(); conn.close()
    if not row: return jsonify({'error': 'Não encontrado'}), 404
    return ok(dict(row))

@app.route('/api/products', methods=['POST'])
@require_auth
def product_create():
    b = request.get_json() or {}
    name = b.get('name', '').strip()
    if not name: return jsonify({'error': 'Nome obrigatório'}), 400
    conn = get_conn(); c = conn.cursor()
    c.execute('''INSERT INTO products (name,category,brand,description,cost_price,sale_price,stock,min_stock,status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
        (name, b.get('category',''), b.get('brand',''), b.get('description',''),
         safe_float(b.get('cost_price')), safe_float(b.get('sale_price')),
         safe_int(b.get('stock')), safe_int(b.get('min_stock'), 5),
         b.get('status','ativo')))
    new_id = c.fetchone()['id']; conn.commit(); conn.close()
    return ok({'id': new_id}, message='Produto cadastrado!'), 201

@app.route('/api/products/<int:pid>', methods=['PUT'])
@require_auth
def product_update(pid):
    b = request.get_json() or {}
    name = b.get('name', '').strip()
    if not name: return jsonify({'error': 'Nome obrigatório'}), 400
    conn = get_conn(); c = conn.cursor()
    c.execute('''UPDATE products SET name=%s,category=%s,brand=%s,description=%s,
        cost_price=%s,sale_price=%s,stock=%s,min_stock=%s,status=%s WHERE id=%s''',
        (name, b.get('category',''), b.get('brand',''), b.get('description',''),
         safe_float(b.get('cost_price')), safe_float(b.get('sale_price')),
         safe_int(b.get('stock')), safe_int(b.get('min_stock'), 5),
         b.get('status','ativo'), pid))
    conn.commit(); conn.close()
    return ok(message='Produto atualizado!')

@app.route('/api/products/<int:pid>', methods=['DELETE'])
@require_auth
def product_delete(pid):
    conn = get_conn(); c = conn.cursor()
    c.execute("DELETE FROM products WHERE id=%s", (pid,))
    conn.commit(); conn.close()
    return ok(message='Produto excluído!')


# ── Supplier Orders ───────────────────────────────────────────────────────────
@app.route('/api/orders', methods=['GET'])
@require_auth
def orders_list():
    conn = get_conn(); c = conn.cursor()
    q = request.args.get('q', '')
    if q:
        c.execute("SELECT * FROM supplier_orders WHERE supplier ILIKE %s OR order_date ILIKE %s ORDER BY order_date DESC",
                  (f'%{q}%', f'%{q}%'))
    else:
        c.execute("SELECT * FROM supplier_orders ORDER BY order_date DESC")
    conn.close()
    return ok(rows_to_list(c.fetchall()))

@app.route('/api/orders/<int:oid>', methods=['GET'])
@require_auth
def order_get(oid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM supplier_orders WHERE id=%s", (oid,))
    order = c.fetchone()
    if not order: conn.close(); return jsonify({'error': 'Não encontrado'}), 404
    c.execute('''SELECT soi.*, p.name product_name FROM supplier_order_items soi
        JOIN products p ON soi.product_id=p.id WHERE soi.order_id=%s''', (oid,))
    items = rows_to_list(c.fetchall())
    conn.close()
    return ok({'order': dict(order), 'items': items})

@app.route('/api/orders', methods=['POST'])
@require_auth
def order_create():
    b = request.get_json() or {}
    supplier = b.get('supplier', '').strip()
    order_date = b.get('order_date', '').strip()
    if not supplier or not order_date:
        return jsonify({'error': 'Fornecedor e data obrigatórios'}), 400
    freight = safe_float(b.get('freight'))
    items   = b.get('items', [])
    if not items:
        return jsonify({'error': 'Adicione pelo menos um produto'}), 400

    conn = get_conn(); c = conn.cursor()
    total_prods = sum(safe_float(it.get('unit_cost')) * safe_int(it.get('quantity')) for it in items)
    total_order = total_prods + freight
    c.execute('''INSERT INTO supplier_orders (order_date,supplier,freight,notes,total_products,total_order)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING id''',
        (order_date, supplier, freight, b.get('notes',''), total_prods, total_order))
    oid = c.fetchone()['id']
    for it in items:
        pid = safe_int(it.get('product_id'))
        qty = safe_int(it.get('quantity'))
        uc  = safe_float(it.get('unit_cost'))
        tc  = qty * uc
        c.execute("INSERT INTO supplier_order_items (order_id,product_id,quantity,unit_cost,total_cost) VALUES (%s,%s,%s,%s,%s)",
                  (oid, pid, qty, uc, tc))
        c.execute("UPDATE products SET stock=stock+%s, cost_price=%s WHERE id=%s", (qty, uc, pid))
    conn.commit(); conn.close()
    return ok({'id': oid}, message='Pedido registrado!'), 201

@app.route('/api/orders/<int:oid>', methods=['DELETE'])
@require_auth
def order_delete(oid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM supplier_order_items WHERE order_id=%s", (oid,))
    items = c.fetchall()
    for it in items:
        c.execute("UPDATE products SET stock=GREATEST(0,stock-%s) WHERE id=%s",
                  (it['quantity'], it['product_id']))
    c.execute("DELETE FROM supplier_orders WHERE id=%s", (oid,))
    conn.commit(); conn.close()
    return ok(message='Pedido excluído!')


# ── Sales ─────────────────────────────────────────────────────────────────────
@app.route('/api/sales', methods=['GET'])
@require_auth
def sales_list():
    conn = get_conn(); c = conn.cursor()
    q  = request.args.get('q', '')
    sf = request.args.get('status', '')
    pf = request.args.get('payment', '')
    sql = '''SELECT s.*, p.name product_name FROM sales s
             JOIN products p ON s.product_id=p.id WHERE 1=1'''
    params = []
    if q:
        sql += ' AND (s.customer_name ILIKE %s OR s.customer_city ILIKE %s OR p.name ILIKE %s OR s.sale_date ILIKE %s)'
        params += [f'%{q}%']*4
    if sf:  sql += ' AND s.payment_status=%s'; params.append(sf)
    if pf:  sql += ' AND s.payment_method=%s'; params.append(pf)
    sql += ' ORDER BY s.created_at DESC'
    c.execute(sql, params); rows = rows_to_list(c.fetchall())
    conn.close()
    return ok(rows)

@app.route('/api/sales', methods=['POST'])
@require_auth
def sale_create():
    b = request.get_json() or {}
    sale_date  = b.get('sale_date', '').strip()
    product_id = safe_int(b.get('product_id'))
    cust_name  = b.get('customer_name', '').strip()
    if not all([sale_date, product_id, cust_name]):
        return jsonify({'error': 'Data, produto e cliente obrigatórios'}), 400

    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM products WHERE id=%s", (product_id,))
    product = c.fetchone()
    if not product:
        conn.close(); return jsonify({'error': 'Produto não encontrado'}), 404

    quantity   = safe_int(b.get('quantity'), 1)
    unit_price = safe_float(b.get('unit_price'))
    if product['stock'] < quantity:
        conn.close()
        return jsonify({'error': f'Estoque insuficiente! Disponível: {product["stock"]} un.'}), 400

    total_price = quantity * unit_price
    cost_total  = quantity * product['cost_price']
    profit      = total_price - cost_total

    cust_city  = b.get('customer_city', '')
    cust_phone = b.get('customer_phone', '')

    # Get or create customer
    c.execute("SELECT id FROM customers WHERE name=%s AND city=%s", (cust_name, cust_city))
    cust = c.fetchone()
    if cust:
        cust_id = cust['id']
    else:
        c.execute("INSERT INTO customers (name,city,phone) VALUES (%s,%s,%s) RETURNING id",
                  (cust_name, cust_city, cust_phone or None))
        cust_id = c.fetchone()['id']

    payment_status = b.get('payment_status', 'pago')
    c.execute('''INSERT INTO sales
        (sale_date,product_id,quantity,customer_id,customer_name,customer_city,customer_phone,
         unit_price,total_price,cost_price,profit,payment_method,payment_status,notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
        (sale_date, product_id, quantity, cust_id, cust_name, cust_city, cust_phone,
         unit_price, total_price, cost_total, profit,
         b.get('payment_method','pix'), payment_status, b.get('notes','')))
    new_id = c.fetchone()['id']
    c.execute("UPDATE products SET stock=stock-%s WHERE id=%s", (quantity, product_id))
    if payment_status == 'pago':
        c.execute("UPDATE customers SET total_spent=total_spent+%s, total_orders=total_orders+1 WHERE id=%s",
                  (total_price, cust_id))
    else:
        c.execute("UPDATE customers SET total_orders=total_orders+1 WHERE id=%s", (cust_id,))
    conn.commit(); conn.close()
    return ok({'id': new_id}, message='Venda registrada!'), 201

@app.route('/api/sales/<int:sid>', methods=['PUT'])
@require_auth
def sale_update(sid):
    b = request.get_json() or {}
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM sales WHERE id=%s", (sid,))
    sale = c.fetchone()
    if not sale: conn.close(); return jsonify({'error': 'Não encontrado'}), 404
    old_status     = sale['payment_status']
    payment_status = b.get('payment_status', old_status)
    if old_status == 'pendente' and payment_status == 'pago' and sale['customer_id']:
        c.execute("UPDATE customers SET total_spent=total_spent+%s WHERE id=%s",
                  (sale['total_price'], sale['customer_id']))
    c.execute("UPDATE sales SET payment_status=%s, payment_method=%s, notes=%s WHERE id=%s",
              (payment_status, b.get('payment_method', sale['payment_method']),
               b.get('notes', sale['notes'] or ''), sid))
    conn.commit(); conn.close()
    return ok(message='Venda atualizada!')

@app.route('/api/sales/<int:sid>/pay', methods=['POST'])
@require_auth
def sale_pay(sid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM sales WHERE id=%s", (sid,))
    s = c.fetchone()
    if s and s['payment_status'] == 'pendente':
        c.execute("UPDATE sales SET payment_status='pago' WHERE id=%s", (sid,))
        if s['customer_id']:
            c.execute("UPDATE customers SET total_spent=total_spent+%s WHERE id=%s",
                      (s['total_price'], s['customer_id']))
        conn.commit()
    conn.close()
    return ok(message='Venda marcada como paga!')

@app.route('/api/sales/<int:sid>', methods=['DELETE'])
@require_auth
def sale_delete(sid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM sales WHERE id=%s", (sid,))
    s = c.fetchone()
    if s:
        c.execute("UPDATE products SET stock=stock+%s WHERE id=%s", (s['quantity'], s['product_id']))
        if s['customer_id']:
            c.execute("UPDATE customers SET total_orders=GREATEST(0,total_orders-1), total_spent=GREATEST(0,total_spent-%s) WHERE id=%s",
                      (s['total_price'] if s['payment_status']=='pago' else 0, s['customer_id']))
        c.execute("DELETE FROM sales WHERE id=%s", (sid,))
        conn.commit()
    conn.close()
    return ok(message='Venda excluída!')


# ── Customers ─────────────────────────────────────────────────────────────────
@app.route('/api/customers', methods=['GET'])
@require_auth
def customers_list():
    conn = get_conn(); c = conn.cursor()
    q = request.args.get('q', '')
    if q:
        c.execute("SELECT * FROM customers WHERE name ILIKE %s OR city ILIKE %s OR phone ILIKE %s ORDER BY total_spent DESC",
                  (f'%{q}%', f'%{q}%', f'%{q}%'))
    else:
        c.execute("SELECT * FROM customers ORDER BY total_spent DESC")
    rows = rows_to_list(c.fetchall()); conn.close()
    return ok(rows)

@app.route('/api/customers/<int:cid>', methods=['GET'])
@require_auth
def customer_get(cid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM customers WHERE id=%s", (cid,))
    cust = c.fetchone()
    if not cust: conn.close(); return jsonify({'error': 'Não encontrado'}), 404
    c.execute('''SELECT s.*, p.name product_name FROM sales s
        JOIN products p ON s.product_id=p.id WHERE s.customer_id=%s ORDER BY s.sale_date DESC''', (cid,))
    purchases = rows_to_list(c.fetchall())
    conn.close()
    return ok({'customer': dict(cust), 'purchases': purchases})

@app.route('/api/customers', methods=['POST'])
@require_auth
def customer_create():
    b = request.get_json() or {}
    name = b.get('name', '').strip()
    if not name: return jsonify({'error': 'Nome obrigatório'}), 400
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO customers (name,city,phone,birthday,notes,vip) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
              (name, b.get('city',''), b.get('phone',''), b.get('birthday') or None,
               b.get('notes',''), 1 if b.get('vip') else 0))
    new_id = c.fetchone()['id']; conn.commit(); conn.close()
    return ok({'id': new_id}, message='Cliente cadastrado!'), 201

@app.route('/api/customers/<int:cid>', methods=['PUT'])
@require_auth
def customer_update(cid):
    b = request.get_json() or {}
    name = b.get('name','').strip()
    if not name: return jsonify({'error': 'Nome obrigatório'}), 400
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE customers SET name=%s,city=%s,phone=%s,birthday=%s,notes=%s,vip=%s WHERE id=%s",
              (name, b.get('city',''), b.get('phone',''), b.get('birthday') or None,
               b.get('notes',''), 1 if b.get('vip') else 0, cid))
    conn.commit(); conn.close()
    return ok(message='Cliente atualizado!')

@app.route('/api/customers/<int:cid>', methods=['DELETE'])
@require_auth
def customer_delete(cid):
    conn = get_conn(); c = conn.cursor()
    c.execute("DELETE FROM customers WHERE id=%s", (cid,))
    conn.commit(); conn.close()
    return ok(message='Cliente excluído!')

@app.route('/api/customers/by-phone', methods=['GET'])
@require_auth
def customer_by_phone():
    phone = request.args.get('phone', '').strip()
    if not phone: return ok(None)
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM customers WHERE phone=%s", (phone,))
    row = c.fetchone(); conn.close()
    return ok(dict(row) if row else None)


# ── Stock ─────────────────────────────────────────────────────────────────────
@app.route('/api/stock', methods=['GET'])
@require_auth
def stock_list():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM products WHERE status='ativo' ORDER BY stock ASC")
    rows = rows_to_list(c.fetchall())
    c.execute("SELECT COALESCE(SUM(stock*cost_price),0) cv, COALESCE(SUM(stock*sale_price),0) sv FROM products WHERE status='ativo'")
    totals = dict(c.fetchone()); conn.close()
    return ok({'products': rows, 'total_cost_value': float(totals['cv']),
               'total_sale_value': float(totals['sv']),
               'potential_profit': float(totals['sv']) - float(totals['cv'])})

@app.route('/api/stock/<int:pid>/min', methods=['PUT'])
@require_auth
def stock_set_min(pid):
    b = request.get_json() or {}
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE products SET min_stock=%s WHERE id=%s", (safe_int(b.get('min_stock', 5)), pid))
    conn.commit(); conn.close()
    return ok(message='Estoque mínimo atualizado!')


# ── Expenses ──────────────────────────────────────────────────────────────────
@app.route('/api/expenses', methods=['GET'])
@require_auth
def expenses_list():
    conn = get_conn(); c = conn.cursor()
    q   = request.args.get('q', '')
    cat = request.args.get('cat', '')
    sql = "SELECT * FROM expenses WHERE 1=1"; params = []
    if q:   sql += " AND (description ILIKE %s OR category ILIKE %s)"; params += [f'%{q}%', f'%{q}%']
    if cat: sql += " AND category=%s"; params.append(cat)
    sql += " ORDER BY expense_date DESC"
    c.execute(sql, params); rows = rows_to_list(c.fetchall())
    c.execute("SELECT COALESCE(SUM(amount),0) total FROM expenses"); total = float(list(c.fetchone().values())[0])
    c.execute("SELECT DISTINCT category FROM expenses ORDER BY category"); cats = [r['category'] for r in c.fetchall()]
    conn.close()
    return ok({'expenses': rows, 'total': total, 'categories': cats})

@app.route('/api/expenses', methods=['POST'])
@require_auth
def expense_create():
    b = request.get_json() or {}
    desc = b.get('description', '').strip(); cat = b.get('category', '').strip()
    if not desc or not cat: return jsonify({'error': 'Descrição e categoria obrigatórias'}), 400
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO expenses (expense_date,category,description,amount,notes) VALUES (%s,%s,%s,%s,%s) RETURNING id",
              (b.get('expense_date',''), cat, desc, safe_float(b.get('amount')), b.get('notes','')))
    new_id = c.fetchone()['id']; conn.commit(); conn.close()
    return ok({'id': new_id}, message='Despesa registrada!'), 201

@app.route('/api/expenses/<int:eid>', methods=['DELETE'])
@require_auth
def expense_delete(eid):
    conn = get_conn(); c = conn.cursor()
    c.execute("DELETE FROM expenses WHERE id=%s", (eid,))
    conn.commit(); conn.close()
    return ok(message='Despesa excluída!')


# ── Goals ─────────────────────────────────────────────────────────────────────
@app.route('/api/goals', methods=['GET'])
@require_auth
def goals_list():
    conn = get_conn(); c = conn.cursor()
    today = date.today().isoformat()
    c.execute("SELECT * FROM goals ORDER BY period_start DESC")
    goals_raw = rows_to_list(c.fetchall())
    result = []
    for g in goals_raw:
        if g['goal_type'] == 'vendas':
            c.execute("SELECT COALESCE(SUM(total_price),0) v FROM sales WHERE payment_status='pago' AND sale_date BETWEEN %s AND %s",
                      (g['period_start'], g['period_end']))
        elif g['goal_type'] == 'lucro':
            c.execute("SELECT COALESCE(SUM(profit),0) v FROM sales WHERE payment_status='pago' AND sale_date BETWEEN %s AND %s",
                      (g['period_start'], g['period_end']))
        else:
            c.execute("SELECT COUNT(*) v FROM sales WHERE sale_date BETWEEN %s AND %s",
                      (g['period_start'], g['period_end']))
        achieved = float(list(c.fetchone().values())[0])
        pct = min(int((achieved / g['target_value']) * 100), 100) if g['target_value'] > 0 else 0
        result.append({**g, 'achieved': achieved, 'pct': pct,
                       'is_active': g['period_start'] <= today <= g['period_end']})
    conn.close()
    return ok(result)

@app.route('/api/goals', methods=['POST'])
@require_auth
def goal_create():
    b = request.get_json() or {}
    title = b.get('title','').strip()
    if not title: return jsonify({'error': 'Título obrigatório'}), 400
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO goals (title,goal_type,target_value,period_start,period_end,notes) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
              (title, b.get('goal_type','vendas'), safe_float(b.get('target_value')),
               b.get('period_start',''), b.get('period_end',''), b.get('notes','')))
    new_id = c.fetchone()['id']; conn.commit(); conn.close()
    return ok({'id': new_id}, message='Meta criada!'), 201

@app.route('/api/goals/<int:gid>', methods=['DELETE'])
@require_auth
def goal_delete(gid):
    conn = get_conn(); c = conn.cursor()
    c.execute("DELETE FROM goals WHERE id=%s", (gid,))
    conn.commit(); conn.close()
    return ok(message='Meta excluída!')


# ── Reports ───────────────────────────────────────────────────────────────────
@app.route('/api/reports', methods=['GET'])
@require_auth
def reports():
    from datetime import timedelta
    import calendar as cal
    conn = get_conn(); c = conn.cursor()
    period    = request.args.get('period', 'month')
    date_from = request.args.get('date_from', '')
    date_to   = request.args.get('date_to', '')
    today     = date.today()

    if period == 'today':    date_from = date_to = today.isoformat()
    elif period == 'week':   date_from = (today - timedelta(days=7)).isoformat(); date_to = today.isoformat()
    elif period == 'month':  date_from = today.replace(day=1).isoformat(); date_to = today.isoformat()
    elif period == 'year':   date_from = today.replace(month=1,day=1).isoformat(); date_to = today.isoformat()

    p=[]; df=''
    if date_from and date_to: df=' AND s.sale_date BETWEEN %s AND %s'; p=[date_from, date_to]
    op=[]; of=''
    if date_from and date_to: of=' WHERE order_date BETWEEN %s AND %s'; op=[date_from, date_to]
    ep=[]; ef=''
    if date_from and date_to: ef=' WHERE expense_date BETWEEN %s AND %s'; ep=[date_from, date_to]

    def scalar(sql, params=()):
        c.execute(sql, params); r=c.fetchone()
        return float(list(r.values())[0]) if r else 0.0

    total_gross   = scalar(f"SELECT COALESCE(SUM(total_price),0) FROM sales s WHERE payment_status='pago'{df}", p)
    total_profit  = scalar(f"SELECT COALESCE(SUM(profit),0) FROM sales s WHERE payment_status='pago'{df}", p)
    total_pending = scalar(f"SELECT COALESCE(SUM(total_price),0) FROM sales s WHERE payment_status='pendente'{df}", p)
    sales_count   = scalar(f"SELECT COUNT(*) FROM sales s WHERE 1=1{df}", p)
    ticket_medio  = (total_gross / sales_count) if sales_count > 0 else 0
    total_invested= scalar(f"SELECT COALESCE(SUM(total_order),0) FROM supplier_orders{of}", op)
    total_expenses= scalar(f"SELECT COALESCE(SUM(amount),0) FROM expenses{ef}", ep)
    net_result    = total_profit - total_expenses

    c.execute(f'''SELECT p.name, SUM(s.quantity) qty, SUM(s.total_price) revenue, SUM(s.profit) profit
        FROM sales s JOIN products p ON s.product_id=p.id WHERE 1=1{df}
        GROUP BY p.id, p.name ORDER BY qty DESC LIMIT 10''', p)
    top_products = rows_to_list(c.fetchall())

    c.execute(f'''SELECT s.customer_name, s.customer_city, COUNT(*) orders, SUM(s.total_price) spent
        FROM sales s WHERE 1=1{df} GROUP BY s.customer_id, s.customer_name, s.customer_city
        ORDER BY spent DESC LIMIT 10''', p)
    top_customers = rows_to_list(c.fetchall())

    c.execute(f'''SELECT payment_method, COUNT(*) cnt, SUM(total_price) total
        FROM sales s WHERE payment_status='pago'{df} GROUP BY payment_method''', p)
    by_payment = rows_to_list(c.fetchall())

    c.execute(f'''SELECT p.category, SUM(s.quantity) qty, SUM(s.total_price) revenue
        FROM sales s JOIN products p ON s.product_id=p.id WHERE 1=1{df}
        GROUP BY p.category ORDER BY revenue DESC''', p)
    by_category = rows_to_list(c.fetchall())

    c.execute(f'''SELECT s.*, p.name product_name FROM sales s
        JOIN products p ON s.product_id=p.id WHERE s.payment_status='pendente'{df}
        ORDER BY s.sale_date DESC''', p)
    pending_sales = rows_to_list(c.fetchall())

    c.execute("SELECT * FROM products WHERE stock<=min_stock AND status='ativo' ORDER BY stock")
    low_stock = rows_to_list(c.fetchall())

    c.execute(f"SELECT * FROM supplier_orders{of} ORDER BY order_date DESC", op)
    sup_orders = rows_to_list(c.fetchall())

    c.execute(f"SELECT * FROM expenses{ef} ORDER BY expense_date DESC", ep)
    exp_list = rows_to_list(c.fetchall())

    conn.close()
    return ok({
        'period': period, 'date_from': date_from, 'date_to': date_to,
        'total_gross': total_gross, 'total_profit': total_profit,
        'total_pending': total_pending, 'sales_count': int(sales_count),
        'ticket_medio': ticket_medio, 'total_invested': total_invested,
        'total_expenses': total_expenses, 'net_result': net_result,
        'top_products': top_products, 'top_customers': top_customers,
        'by_payment': by_payment, 'by_category': by_category,
        'pending_sales': pending_sales, 'low_stock': low_stock,
        'sup_orders': sup_orders, 'exp_list': exp_list,
    })


# ── Excel Import ──────────────────────────────────────────────────────────────
@app.route('/api/import/excel', methods=['POST'])
@require_auth
def import_excel():
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado'}), 400
    f = request.files['file']
    if not f.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'Formato inválido'}), 400

    import tempfile, os
    from migrate_postgres import migrate_pg

    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name

    try:
        result = migrate_pg(tmp_path)
    finally:
        try: os.remove(tmp_path)
        except: pass

    return ok(result)


# ── Health check ──────────────────────────────────────────────────────────────
@app.route('/api/health', methods=['GET'])
def health():
    return ok(status='ok', version='3.0')


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
