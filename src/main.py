from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, date
from calendar import monthrange
from datetime import datetime

app = Flask(__name__)
app.jinja_env.globals['now'] = datetime.now
app.secret_key = os.urandom(24)
DATABASE = 'finance.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Cria as tabelas se não existirem."""
    if not os.path.exists(DATABASE):
        conn = get_db_connection()
        cursor = conn.cursor()

        # Tabela de usuários (inclui campo 'last_checkin')
        cursor.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                last_checkin TEXT  -- data do último checkin (YYYY-MM-DD)
            )
        """)

        # Tabela de transações (inclui recorrência)
        cursor.execute("""
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,  -- 'credito' ou 'debito'
                amount REAL NOT NULL,
                tag TEXT,
                date_launch TEXT,    -- data de lançamento (YYYY-MM-DD)
                date_due TEXT,       -- data de vencimento/recebimento
                paid INTEGER,        -- 1 se pago/recebido, 0 caso contrário
                paid_date TEXT,      -- data em que foi pago/recebido
                description TEXT,
                recurring INTEGER,   -- 1 se recorrente, 0 se não
                end_recurrence TEXT, -- data limite da recorrência (YYYY-MM-DD)
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        # Tabela de orçamentos (budgets): por tag e valor mensal
        cursor.execute("""
            CREATE TABLE budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                monthly_amount REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        # Tabela de objetivos (goals): ex: "Comprar um carro" com meta e prazo
        cursor.execute("""
            CREATE TABLE goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                goal_name TEXT NOT NULL,
                target_amount REAL NOT NULL,
                deadline TEXT,   -- YYYY-MM-DD
                current_amount REAL DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        # Tabela de objetivos (goals)
        # Certifique-se de ter as colunas: (id, user_id, goal_name, target_amount, deadline, current_amount)
        cursor.execute("""
            CREATE TABLE goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                goal_name TEXT NOT NULL,
                target_amount REAL NOT NULL,
                deadline TEXT,
                current_amount REAL DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        conn.commit()
        conn.close()

init_db()


@app.template_filter('currency')
def currency_format(value):
    try:
        # Formata para pt-BR: separador de milhar como ponto e decimal como vírgula
        return "R$ " + "{:,.2f}".format(value).replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception as e:
        return value

@app.template_filter('date_br')
def date_br(value):
    if value:
        try:
            d = datetime.strptime(value, "%Y-%m-%d")
            return d.strftime("%d/%m/%Y")
        except Exception as e:
            return value
    return value

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, generate_password_hash(password))
            )
            conn.commit()
            flash("Usuário registrado com sucesso!", "success")
            flash("account_created", "info")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("Nome de usuário já existe!", "danger")
        finally:
            conn.close()
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('dashboard'))
        else:
            flash("Credenciais inválidas.", "danger")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/checkin', methods=['POST'])
def checkin():
    """Registra a data do último checkin como a data atual."""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    cursor = conn.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("UPDATE users SET last_checkin = ? WHERE id = ?", (today_str, session['user_id']))
    conn.commit()
    conn.close()
    flash("Check-in realizado com sucesso! Continue acompanhando suas contas!", "info")
    return redirect(url_for('dashboard'))

import math

@app.route('/')
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()

    # Último checkin e último lançamento
    cursor.execute("SELECT last_checkin FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    last_checkin = user['last_checkin'] if user and user['last_checkin'] else "Nunca"

    cursor.execute("""
        SELECT date_launch FROM transactions
        WHERE user_id = ?
        ORDER BY date_launch DESC
        LIMIT 1
    """, (user_id,))
    row_launch = cursor.fetchone()
    last_launch = row_launch['date_launch'] if row_launch else "Nenhum lançamento"

    # Parâmetro de período e mês selecionado
    timeframe = request.args.get('timeframe', 'mensal')
    selected_month = request.args.get('month')
    now_dt = datetime.now()
    if not selected_month:
        selected_month = now_dt.strftime("%Y-%m")
    year, month = map(int, selected_month.split('-'))
    days_in_month = monthrange(year, month)[1]

    # Dados para o gráfico (mantém os mesmos arrays)
    line_labels = []
    forecast_debits = []
    actual_debits = []
    forecast_credits = []
    actual_credits = []
    for day in range(1, days_in_month + 1):
        day_str = f"{year}-{month:02d}-{day:02d}"
        label = f"{day:02d}/{month:02d}"
        line_labels.append(label)
        cursor.execute("""
            SELECT SUM(amount) as total
            FROM transactions
            WHERE user_id = ? AND type = 'debito' AND date_due = ?
        """, (user_id, day_str))
        row = cursor.fetchone()
        forecast_debits.append(row['total'] if row['total'] else 0)
        cursor.execute("""
            SELECT SUM(amount) as total
            FROM transactions
            WHERE user_id = ? AND type = 'debito' AND date_due = ? AND paid = 1
        """, (user_id, day_str))
        row = cursor.fetchone()
        actual_debits.append(row['total'] if row['total'] else 0)
        cursor.execute("""
            SELECT SUM(amount) as total
            FROM transactions
            WHERE user_id = ? AND type = 'credito' AND date_due = ?
        """, (user_id, day_str))
        row = cursor.fetchone()
        forecast_credits.append(row['total'] if row['total'] else 0)
        cursor.execute("""
            SELECT SUM(amount) as total
            FROM transactions
            WHERE user_id = ? AND type = 'credito' AND date_due = ? AND paid = 1
        """, (user_id, day_str))
        row = cursor.fetchone()
        actual_credits.append(row['total'] if row['total'] else 0)

    # Cálculo de totais para o mês (usando date_due)
    cursor.execute("""
        SELECT SUM(amount) as total_credit
        FROM transactions
        WHERE user_id = ? AND type = 'credito' AND date_due LIKE ?
    """, (user_id, selected_month + '%'))
    row_credit = cursor.fetchone()
    total_credit = row_credit['total_credit'] if row_credit['total_credit'] else 0

    cursor.execute("""
        SELECT SUM(amount) as actual_credit
        FROM transactions
        WHERE user_id = ? AND type = 'credito' AND date_due LIKE ? AND paid = 1
    """, (user_id, selected_month + '%'))
    row_act_credit = cursor.fetchone()
    actual_credit = row_act_credit['actual_credit'] if row_act_credit['actual_credit'] else 0

    cursor.execute("""
        SELECT SUM(amount) as total_debit
        FROM transactions
        WHERE user_id = ? AND type = 'debito' AND date_due LIKE ?
    """, (user_id, selected_month + '%'))
    row_debit = cursor.fetchone()
    total_debit = row_debit['total_debit'] if row_debit['total_debit'] else 0

    cursor.execute("""
        SELECT SUM(amount) as actual_debit
        FROM transactions
        WHERE user_id = ? AND type = 'debito' AND date_due LIKE ? AND paid = 1
    """, (user_id, selected_month + '%'))
    row_act_debit = cursor.fetchone()
    actual_debit = row_act_debit['actual_debit'] if row_act_debit['actual_debit'] else 0

    efficiency_credit = (actual_credit / total_credit * 100) if total_credit > 0 else None
    efficiency_debit = (actual_debit / total_debit * 100) if total_debit > 0 else None
    lucro_preju = total_credit - total_debit

    # Paginação: Defina 10 itens por página
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page

    # Total de transações do usuário (para calcular número de páginas)
    cursor.execute("SELECT COUNT(*) as total FROM transactions WHERE user_id = ?", (user_id,))
    total_transactions = cursor.fetchone()['total']
    total_pages = math.ceil(total_transactions / per_page)

    cursor.execute("""
        SELECT * FROM transactions
        WHERE user_id = ?
        ORDER BY date_launch DESC
        LIMIT ? OFFSET ?
    """, (user_id, per_page, offset))
    transactions = cursor.fetchall()

    # Buscar Objetivos
    cursor.execute("""
        SELECT id, goal_name, target_amount, deadline, current_amount
        FROM goals
        WHERE user_id = ?
    """, (user_id,))
    goals_list = cursor.fetchall()

    conn.close()
    return render_template('dashboard.html',
                           timeframe=timeframe,
                           selected_month=selected_month,
                           last_checkin=last_checkin,
                           last_launch=last_launch,
                           line_labels=line_labels,
                           forecast_debits=forecast_debits,
                           actual_debits=actual_debits,
                           forecast_credits=forecast_credits,
                           actual_credits=actual_credits,
                           total_credit=total_credit,
                           actual_credit=actual_credit,
                           efficiency_credit=efficiency_credit,
                           total_debit=total_debit,
                           actual_debit=actual_debit,
                           efficiency_debit=efficiency_debit,
                           lucro_preju=lucro_preju,
                           transactions=transactions,
                           goals_list=goals_list,
                           page=page,
                           total_pages=total_pages)


@app.route('/transactions_ajax')
def transactions_ajax():
    if 'user_id' not in session:
        return "", 401

    user_id = session['user_id']
    page = request.args.get('page', 1, type=int)
    timeframe = request.args.get('timeframe', 'mensal')
    selected_month = request.args.get('month')
    if not selected_month:
        selected_month = datetime.now().strftime("%Y-%m")
    per_page = 10
    offset = (page - 1) * per_page

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total FROM transactions WHERE user_id = ?", (user_id,))
    total_transactions = cursor.fetchone()['total']
    total_pages = math.ceil(total_transactions / per_page)

    cursor.execute("""
        SELECT * FROM transactions
        WHERE user_id = ?
        ORDER BY date_launch DESC
        LIMIT ? OFFSET ?
    """, (user_id, per_page, offset))
    transactions = cursor.fetchall()
    conn.close()

    return render_template('transactions_partial.html',
                           transactions=transactions,
                           page=page,
                           total_pages=total_pages,
                           timeframe=timeframe,
                           selected_month=selected_month)


@app.route('/add_transaction', methods=['GET', 'POST'])
def add_transaction():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    preselected_type = request.args.get('type', '') 
    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar tags existentes para exibir no combo
    cursor.execute("""
        SELECT DISTINCT tag FROM transactions
        WHERE user_id = ? AND tag IS NOT NULL AND tag <> ''
    """, (session['user_id'],))
    existing_tags = [row['tag'] for row in cursor.fetchall()]

    if request.method == 'POST':
        user_id = session['user_id']
        t_type = request.form['type'] 
        amount = float(request.form['amount'])
        chosen_tag = request.form['tag_select']
        new_tag = request.form.get('new_tag') or ''

        # Decide qual tag usar
        if chosen_tag == 'new' and new_tag.strip() != '':
            tag = new_tag.strip()
        else:
            tag = chosen_tag if chosen_tag != 'new' else ''

        date_launch = request.form['date_launch']
        date_due = request.form['date_due']
        paid = 1 if request.form.get('paid') == 'on' else 0
        paid_date = request.form['paid_date'] if paid and request.form['paid_date'] else None
        description = request.form['description']

        # Campos de recorrência
        recurring = 1 if request.form.get('recurring') == 'on' else 0
        end_recurrence = request.form['end_recurrence'] if recurring else None

        cursor.execute("""
            INSERT INTO transactions 
            (user_id, type, amount, tag, date_launch, date_due, paid, paid_date, description, recurring, end_recurrence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, t_type, amount, tag, date_launch, date_due, paid, paid_date, description, recurring, end_recurrence))
        conn.commit()
        conn.close()

        # Feedback visual extra (flash + toast)
        flash("Transação adicionada com sucesso! Parabéns, você está se organizando melhor!", "success")
        return redirect(url_for('dashboard'))

    conn.close()
    return render_template('add_transaction.html', existing_tags=existing_tags, current_year=datetime.now().year)

@app.route('/edit_transaction/<int:transaction_id>', methods=['GET', 'POST'])
def edit_transaction(transaction_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar transação
    cursor.execute("SELECT * FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, session['user_id']))
    transaction = cursor.fetchone()

    # Buscar tags existentes
    cursor.execute("""
        SELECT DISTINCT tag FROM transactions
        WHERE user_id = ? AND tag IS NOT NULL AND tag <> ''
    """, (session['user_id'],))
    existing_tags = [row['tag'] for row in cursor.fetchall()]

    if not transaction:
        conn.close()
        flash("Transação não encontrada", "danger")
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        t_type = request.form['type']
        amount = float(request.form['amount'])
        chosen_tag = request.form['tag_select']
        new_tag = request.form.get('new_tag') or ''
        if chosen_tag == 'new' and new_tag.strip() != '':
            tag = new_tag.strip()
        else:
            tag = chosen_tag if chosen_tag != 'new' else ''

        date_launch = datetime.now().strftime("%Y-%m-%d")
        date_due = request.form['date_due']
        paid = 1 if request.form.get('paid') == 'on' else 0
        paid_date = request.form['paid_date'] if paid and request.form['paid_date'] else None
        description = request.form['description']

        # recorrência
        recurring = 1 if request.form.get('recurring') == 'on' else 0
        end_recurrence = request.form['end_recurrence'] if recurring else None

        cursor.execute("""
            UPDATE transactions
            SET type = ?, amount = ?, tag = ?, date_launch = ?, date_due = ?, 
                paid = ?, paid_date = ?, description = ?, recurring = ?, end_recurrence = ?
            WHERE id = ? AND user_id = ?
        """, (t_type, amount, tag, date_launch, date_due, 
              paid, paid_date, description, recurring, end_recurrence,
              transaction_id, session['user_id']))
        conn.commit()
        conn.close()

        flash("Transação atualizada com sucesso!", "success")
        return redirect(url_for('dashboard'))

    conn.close()
    return render_template('edit_transaction.html', transaction=transaction, existing_tags=existing_tags)

@app.route('/delete_transaction/<int:transaction_id>', methods=['POST'])
def delete_transaction(transaction_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, session['user_id']))
    conn.commit()
    conn.close()
    flash("Transação excluída!", "info")
    return redirect(url_for('dashboard'))

# Rotas para as duas novas features (exemplo simplificado)

@app.route('/budgets', methods=['GET', 'POST'])
def budgets():
    """Gerenciamento de orçamentos mensais por tag."""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == 'POST':
        tag = request.form['tag']
        monthly_amount = float(request.form['monthly_amount'])
        cursor.execute("""
            INSERT INTO budgets (user_id, tag, monthly_amount)
            VALUES (?, ?, ?)
        """, (session['user_id'], tag, monthly_amount))
        conn.commit()
        flash("Orçamento cadastrado com sucesso!", "success")

    # Listar orçamentos do usuário
    cursor.execute("SELECT * FROM budgets WHERE user_id = ?", (session['user_id'],))
    budgets_list = cursor.fetchall()
    conn.close()
    return render_template('budgets.html', budgets_list=budgets_list)

@app.route('/goals', methods=['GET', 'POST'])
def goals():
    """Gerencia objetivos: exibe a lista de objetivos e permite atualizar o valor atingido."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()

    # Se o formulário for enviado para criar um novo objetivo
    if request.method == 'POST':
        goal_name = request.form['goal_name']
        target_amount = float(request.form['target_amount'])
        deadline = request.form['deadline'] or None  # pode estar vazio
        # current_amount começa em 0
        cursor.execute("""
            INSERT INTO goals (user_id, goal_name, target_amount, deadline, current_amount)
            VALUES (?, ?, ?, ?, 0)
        """, (user_id, goal_name, target_amount, deadline))
        conn.commit()
        flash("Objetivo criado com sucesso!", "success")

    # Listar objetivos
    cursor.execute("""
        SELECT * FROM goals WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,))
    goals_list = cursor.fetchall()

    conn.close()
    return render_template('goals.html', goals_list=goals_list)

@app.route('/update_goal/<int:goal_id>', methods=['GET', 'POST'])
def update_goal(goal_id):
    """Permite ao usuário editar o valor atual (current_amount) do objetivo."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()

    # Verificar se o objetivo pertence ao usuário
    cursor.execute("SELECT * FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
    goal = cursor.fetchone()
    if not goal:
        conn.close()
        flash("Objetivo não encontrado ou não pertence a você.", "danger")
        return redirect(url_for('goals'))

    if request.method == 'POST':
        new_current_amount = float(request.form['current_amount'])
        cursor.execute("""
            UPDATE goals
            SET current_amount = ?
            WHERE id = ? AND user_id = ?
        """, (new_current_amount, goal_id, user_id))
        conn.commit()
        conn.close()
        flash("Valor atingido atualizado com sucesso!", "success")
        return redirect(url_for('goals'))

    conn.close()
    return render_template('update_goal.html', goal=goal)


if __name__ == '__main__':
    app.run(debug=True)
