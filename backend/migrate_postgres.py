"""
migrate_postgres.py — migra Excel para PostgreSQL
"""
import re
from datetime import datetime, date
from database import get_conn

try:
    import pandas as pd
except ImportError:
    raise SystemExit("pip install pandas openpyxl")


def parse_date(val):
    if val is None: return None
    if isinstance(val, (datetime, date)): return val.strftime('%Y-%m-%d')
    s = str(val).strip()
    for fmt in ('%d/%m/%Y','%Y-%m-%d','%d-%m-%Y','%m/%d/%Y'):
        try: return datetime.strptime(s.split(' ')[0], fmt).strftime('%Y-%m-%d')
        except: continue
    return None

def to_float(v, d=0.0):
    try: return float(str(v).replace(',','.').strip())
    except: return d

def to_int(v, d=0):
    try: return int(float(str(v).strip()))
    except: return d

def clean(v):
    return str(v).strip() if v is not None else ''

def norm_payment(v):
    s = clean(v).lower()
    if 'pix' in s or 'infinity' in s: return 'pix'
    if 'cart' in s: return 'cartao'
    if 'dinh' in s or 'especie' in s: return 'dinheiro'
    if 'fiado' in s or 'prazo' in s: return 'fiado'
    return 'pix'

def norm_status(v):
    return 'pago' if 'pago' in clean(v).lower() else 'pendente'

def guess_brand(name):
    n = name.lower()
    if 'lattafa' in n: return 'Lattafa'
    if 'wataniah' in n: return 'Al Wataniah'
    if 'sahari' in n: return 'Al Sahari'
    return 'Importado'


def migrate_pg(xlsx_path: str) -> dict:
    log = []; counts = {'products':0,'orders':0,'order_items':0,'sales':0,'customers':0,'investments':0}
    errors = []

    def info(m): log.append(('info', m))
    def warn(m): log.append(('warn', m)); errors.append(m)

    try:
        xl = pd.ExcelFile(xlsx_path)
    except Exception as e:
        return {'log': [('error', str(e))], 'counts': counts, 'errors': [str(e)]}

    info(f"Abas encontradas: {xl.sheet_names}")
    conn = get_conn(); c = conn.cursor()

    # ── PRODUCTS ──────────────────────────────────────────────────────────
    info("── Migrando PRODUTOS ──")
    try:
        df = pd.read_excel(xl, sheet_name='Cadastro de Produtos', header=None)
        header_row = next((i for i,r in df.iterrows() if str(r.iloc[0]).strip().lower() in ('código','codigo','id')), 3)
        for idx, row in df.iloc[header_row+1:].iterrows():
            code = clean(row.iloc[0]); name = clean(row.iloc[2]) if len(row)>2 else ''
            if not code or not name or name in ('nan','None',''): continue
            if code.upper() in ('DESC.PIX','FRETE','CÓDIGO'): continue
            try: pid = int(code.lstrip('0') or '0') or int(code)
            except: pid = None
            cost = to_float(row.iloc[3]) if len(row)>3 else 0
            sale = to_float(row.iloc[4]) if len(row)>4 else 0
            if pid:
                c.execute("SELECT id FROM products WHERE id=%s", (pid,))
                if c.fetchone():
                    c.execute("UPDATE products SET name=%s,cost_price=%s,sale_price=%s WHERE id=%s",
                              (name.strip(), cost, sale, pid))
                else:
                    c.execute("""INSERT INTO products (id,name,category,brand,cost_price,sale_price,stock,min_stock,status)
                        VALUES (%s,%s,%s,%s,%s,%s,0,3,'ativo')""",
                        (pid, name.strip(), 'Perfume Árabe', guess_brand(name), cost, sale))
                    counts['products'] += 1
        # Reset sequence
        c.execute("SELECT setval('products_id_seq', COALESCE((SELECT MAX(id) FROM products),1))")
        conn.commit()
        info(f"Produtos: {counts['products']} inseridos.")
    except Exception as e:
        warn(f"Erro produtos: {e}")

    # ── ORDERS ────────────────────────────────────────────────────────────
    info("── Migrando PEDIDOS ──")
    try:
        df = pd.read_excel(xl, sheet_name='Pedidos Fornecedor', header=None)
        current_order = None; current_items = []

        def flush():
            nonlocal current_order, current_items
            if not current_order: return
            o = current_order
            total_prods = sum(it['tc'] for it in current_items)
            total_order = total_prods + o['freight']
            c.execute("""INSERT INTO supplier_orders (order_date,supplier,freight,notes,total_products,total_order)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (o['date'] or date.today().isoformat(), o['supplier'], o['freight'], o['notes'], total_prods, total_order))
            oid = c.fetchone()['id']; counts['orders'] += 1
            info(f"  Pedido #{oid} '{o['supplier']}' — R${total_order:.2f}")
            for it in current_items:
                c.execute("SELECT id FROM products WHERE id=%s", (it['pid'],))
                if not c.fetchone():
                    c.execute("SELECT id FROM products WHERE name ILIKE %s", (f"%{it['name']}%",))
                    row = c.fetchone()
                    it['pid'] = row['id'] if row else None
                if it['pid']:
                    c.execute("INSERT INTO supplier_order_items (order_id,product_id,quantity,unit_cost,total_cost) VALUES (%s,%s,%s,%s,%s)",
                              (oid, it['pid'], it['qty'], it['uc'], it['tc']))
                    c.execute("UPDATE products SET stock=stock+%s, cost_price=%s WHERE id=%s",
                              (it['qty'], it['uc'], it['pid']))
                    counts['order_items'] += 1
            current_order = None; current_items = []

        for idx, row in df.iterrows():
            vals = list(row); col0 = clean(vals[0]); col6 = clean(vals[6]) if len(vals)>6 else ''
            if 'pedido' in col6.lower():
                flush()
                supplier = re.sub(r'^\d+\*?\s*[Pp]edido\s*[-–]\s*', '', col6).strip() or col6
                current_order = {'supplier': supplier, 'date': None, 'freight': 0.0, 'notes': col6}
                current_items = []; continue
            if clean(vals[1]).upper() == 'DESC.PIX' or col0.upper() == 'DESC.PIX':
                try:
                    tp = to_float(vals[5]); pp = to_float(vals[3])
                    if current_order: current_order['freight'] = max(0, tp - pp)
                except: pass
                continue
            if col0.lower() in ('quantidade','qtd') or (col0 == '' and clean(vals[1]) == ''): continue
            if current_order:
                qty = to_int(vals[0]); code = clean(vals[1]); name = clean(vals[2])
                cost = to_float(vals[3]); dt = parse_date(vals[4])
                if code.upper() == 'DESC.PIX' or not name or qty == 0: continue
                if dt and not current_order['date']: current_order['date'] = dt
                try: pid = int(str(code).lstrip('0') or '0') or int(code)
                except: pid = None
                if name: current_items.append({'pid': pid, 'name': name, 'qty': qty, 'uc': cost/qty if qty>0 else cost, 'tc': cost})
        flush()
        conn.commit(); info(f"Pedidos: {counts['orders']} pedidos, {counts['order_items']} itens.")
    except Exception as e:
        warn(f"Erro pedidos: {e}")

    # ── SALES ─────────────────────────────────────────────────────────────
    info("── Migrando VENDAS ──")
    try:
        df = pd.read_excel(xl, sheet_name='Controle Financeiro', header=None)
        header_row = next((i for i,r in df.iterrows() if str(r.iloc[0]).strip().lower()=='data'), 2)
        sales_end = next((i for i in range(header_row+1, len(df)) if clean(df.iloc[i, 0]).upper() in ('INVESTIMENTOS','NOME','SALDO') or (len(df.columns)>6 and str(df.iloc[i,6]).strip()=='Preço de Custo')), len(df))

        for idx in range(header_row+1, sales_end):
            row = list(df.iloc[idx])
            v1 = clean(row[1]) if len(row)>1 else ''
            cust_name = clean(row[2]) if len(row)>2 else ''
            if not v1 or not cust_name or cust_name == 'nan': continue
            sale_date   = parse_date(row[0]) or date.today().isoformat()
            cust_city   = clean(row[3]) if len(row)>3 else ''
            prod_code   = clean(row[4]) if len(row)>4 else ''
            prod_name   = clean(row[5]) if len(row)>5 else ''
            cost_price  = to_float(row[6]) if len(row)>6 else 0
            sale_price  = to_float(row[7]) if len(row)>7 else 0
            profit_val  = to_float(row[8]) if len(row)>8 else 0
            payment     = norm_payment(row[9])
            status      = norm_status(row[10]) if len(row)>10 and row[10] else 'pendente'

            # Find product
            prod_id = None
            if prod_code:
                try:
                    numeric = int(str(prod_code).lstrip('0') or '0') or int(prod_code)
                    c.execute("SELECT id FROM products WHERE id=%s", (numeric,))
                    r = c.fetchone()
                    if r: prod_id = r['id']
                except: pass
            if not prod_id and prod_name:
                c.execute("SELECT id FROM products WHERE name ILIKE %s", (f"%{prod_name.strip()}%",))
                r = c.fetchone()
                if r: prod_id = r['id']
            if not prod_id:
                c.execute("INSERT INTO products (name,category,cost_price,sale_price,stock,min_stock,status) VALUES (%s,'Perfume Árabe',%s,%s,0,3,'ativo') RETURNING id",
                          (prod_name, cost_price, sale_price))
                prod_id = c.fetchone()['id']

            if profit_val == 0 and sale_price > 0 and cost_price > 0:
                profit_val = sale_price - cost_price

            # Customer
            c.execute("SELECT id FROM customers WHERE name=%s AND city=%s", (cust_name, cust_city))
            cust = c.fetchone()
            cust_id = cust['id'] if cust else None
            if not cust_id:
                c.execute("INSERT INTO customers (name,city,phone) VALUES (%s,%s,NULL) RETURNING id",
                          (cust_name, cust_city))
                cust_id = c.fetchone()['id']

            c.execute("""INSERT INTO sales (sale_date,product_id,quantity,customer_id,customer_name,customer_city,
                customer_phone,unit_price,total_price,cost_price,profit,payment_method,payment_status,notes)
                VALUES (%s,%s,1,%s,%s,%s,'',%s,%s,%s,%s,%s,%s,%s)""",
                (sale_date, prod_id, cust_id, cust_name, cust_city,
                 sale_price, sale_price, cost_price, profit_val, payment, status, f'Venda #{v1}'))
            counts['sales'] += 1
            if status == 'pago':
                c.execute("UPDATE customers SET total_spent=total_spent+%s, total_orders=total_orders+1 WHERE id=%s",
                          (sale_price, cust_id))
            else:
                c.execute("UPDATE customers SET total_orders=total_orders+1 WHERE id=%s", (cust_id,))

        conn.commit()
        counts['customers'] = c.execute("SELECT COUNT(*) FROM customers") or 0
        c.execute("SELECT COUNT(*) cnt FROM customers"); counts['customers'] = c.fetchone()['cnt']
        info(f"Vendas: {counts['sales']} migradas.")
    except Exception as e:
        warn(f"Erro vendas: {e}")

    # ── INVESTMENTS ───────────────────────────────────────────────────────
    info("── Migrando INVESTIMENTOS ──")
    try:
        df = pd.read_excel(xl, sheet_name='Controle Financeiro', header=None)
        in_invest = False; investor = None
        for idx, row in df.iterrows():
            v0 = clean(row.iloc[0])
            if v0.upper() == 'INVESTIMENTOS': in_invest = True; continue
            if not in_invest: continue
            if v0.upper() in ('NOME','SALDO',''): continue
            if v0 and v0.upper() not in ('NAN','NONE'): investor = v0.strip().title()
            inv_date = parse_date(row.iloc[1]) if len(row)>1 else None
            inv_val  = to_float(row.iloc[2]) if len(row)>2 else 0
            if inv_val > 0 and inv_date:
                c.execute("INSERT INTO expenses (expense_date,category,description,amount) VALUES (%s,'Investimento',%s,%s)",
                          (inv_date, f'Investimento — {investor or "Sócio"}', inv_val))
                counts['investments'] += 1
        conn.commit(); info(f"Investimentos: {counts['investments']} registrados.")
    except Exception as e:
        warn(f"Erro investimentos: {e}")

    # ── RECALC STOCK ──────────────────────────────────────────────────────
    c.execute("SELECT id FROM products")
    for row in c.fetchall():
        pid = row['id']
        c.execute("SELECT COALESCE(SUM(quantity),0) v FROM supplier_order_items WHERE product_id=%s", (pid,))
        bought = int(list(c.fetchone().values())[0])
        c.execute("SELECT COALESCE(SUM(quantity),0) v FROM sales WHERE product_id=%s", (pid,))
        sold = int(list(c.fetchone().values())[0])
        c.execute("UPDATE products SET stock=%s WHERE id=%s", (max(0, bought-sold), pid))
    c.execute("SELECT setval('products_id_seq', COALESCE((SELECT MAX(id) FROM products),1))")
    conn.commit(); conn.close()
    info("Estoque recalculado. Migração concluída!")

    return {'log': log, 'counts': counts, 'errors': errors}
