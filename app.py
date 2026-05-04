
import os, urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO

from flask import Flask, render_template, request, redirect, url_for, session, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import create_engine, text
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave")

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///oficina_ademar_local.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123456")

CORRECOES = {
    "MECANICA":"MECÂNICA","GERAL":"GERAL","REVISAO":"REVISÃO","SUSPENSAO":"SUSPENSÃO",
    "OLEO":"ÓLEO","CAMBIO":"CÂMBIO","INJECAO":"INJEÇÃO","DIRECAO":"DIREÇÃO",
    "ELETRICA":"ELÉTRICA","CABECOTE":"CABEÇOTE","MAO":"MÃO","PECAS":"PEÇAS",
    "ORCAMENTO":"ORÇAMENTO","VEICULO":"VEÍCULO","SERVICO":"SERVIÇO","SERVICOS":"SERVIÇOS",
    "DEBITO":"DÉBITO","CREDITO":"CRÉDITO","DESCRICAO":"DESCRIÇÃO","OBSERVACAO":"OBSERVAÇÃO",
    "OBSERVACOES":"OBSERVAÇÕES","ALINHAMENTO":"ALINHAMENTO","BALANCEAMENTO":"BALANCEAMENTO"
}

def normalizar(s):
    if s is None:
        return ""
    s = str(s).upper().strip()
    return " ".join(CORRECOES.get(p, p) for p in s.split())

def money(v):
    try:
        return float(str(v or "0").replace(".", "").replace(",", "."))
    except Exception:
        return 0.0

def brl(v):
    return f"R$ {float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

@app.context_processor
def inject_helpers():
    return dict(brl=brl)

def pk_sql():
    if DATABASE_URL.startswith("sqlite"):
        return "INTEGER PRIMARY KEY AUTOINCREMENT"
    return "SERIAL PRIMARY KEY"

def init_db():
    pk = pk_sql()
    with engine.begin() as con:
        con.execute(text(f"""
        CREATE TABLE IF NOT EXISTS usuarios(
            id {pk},
            usuario VARCHAR(120) UNIQUE NOT NULL,
            senha_hash VARCHAR(255) NOT NULL
        );
        """))
        con.execute(text(f"""
        CREATE TABLE IF NOT EXISTS clientes(
            id {pk},
            nome VARCHAR(180) NOT NULL,
            telefone VARCHAR(60),
            endereco VARCHAR(255),
            criado_em VARCHAR(20)
        );
        """))
        con.execute(text(f"""
        CREATE TABLE IF NOT EXISTS veiculos(
            id {pk},
            cliente_id INTEGER,
            modelo VARCHAR(160),
            placa VARCHAR(30),
            ano VARCHAR(20),
            cor VARCHAR(60)
        );
        """))
        con.execute(text(f"""
        CREATE TABLE IF NOT EXISTS orcamentos(
            id {pk},
            cliente_id INTEGER,
            veiculo_id INTEGER,
            data VARCHAR(20),
            status VARCHAR(40),
            descricao TEXT,
            pecas_desc TEXT,
            pecas_valor FLOAT,
            mao_obra_desc TEXT,
            mao_obra_valor FLOAT,
            desconto FLOAT,
            acrescimo FLOAT,
            total FLOAT,
            pagamento VARCHAR(40),
            parcelas INTEGER,
            valor_parcela FLOAT,
            primeira_parcela VARCHAR(20),
            observacoes TEXT
        );
        """))
        con.execute(text(f"""
        CREATE TABLE IF NOT EXISTS financeiro(
            id {pk},
            data VARCHAR(20),
            tipo VARCHAR(30),
            descricao TEXT,
            valor FLOAT,
            forma VARCHAR(40),
            orcamento_id INTEGER,
            parcela_num INTEGER,
            parcelas_total INTEGER,
            status VARCHAR(40)
        );
        """))

        con.execute(text(f"""
        CREATE TABLE IF NOT EXISTS orcamento_itens(
            id {pk},
            orcamento_id INTEGER,
            tipo VARCHAR(30),
            descricao TEXT,
            quantidade FLOAT,
            valor_unitario FLOAT,
            total FLOAT
        );
        """))
        con.execute(text(f"""
        CREATE TABLE IF NOT EXISTS ordens_servico(
            id {pk},
            orcamento_id INTEGER,
            cliente_id INTEGER,
            veiculo_id INTEGER,
            data_abertura VARCHAR(20),
            data_fechamento VARCHAR(20),
            status VARCHAR(40),
            descricao TEXT,
            observacoes TEXT
        );
        """))

        try:
            con.execute(text("ALTER TABLE veiculos ADD COLUMN km VARCHAR(40)"))
        except Exception:
            pass

        row = con.execute(text("SELECT id FROM usuarios WHERE usuario=:u"), {"u": ADMIN_USER}).fetchone()
        if not row:
            con.execute(text("INSERT INTO usuarios(usuario,senha_hash) VALUES(:u,:s)"),
                        {"u": ADMIN_USER, "s": generate_password_hash(ADMIN_PASSWORD)})

init_db()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logado"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def fetchall(sql, params=None):
    with engine.begin() as con:
        return con.execute(text(sql), params or {}).mappings().all()

def fetchone(sql, params=None):
    with engine.begin() as con:
        return con.execute(text(sql), params or {}).mappings().fetchone()

def execute(sql, params=None):
    with engine.begin() as con:
        return con.execute(text(sql), params or {})

@app.route("/login", methods=["GET","POST"])
def login():
    erro = None
    if request.method == "POST":
        usuario = request.form.get("usuario","").strip()
        senha = request.form.get("senha","")
        user = fetchone("SELECT * FROM usuarios WHERE usuario=:u", {"u": usuario})
        if user and check_password_hash(user["senha_hash"], senha):
            session["logado"] = True
            session["usuario"] = usuario
            return redirect(url_for("index"))
        erro = "USUÁRIO OU SENHA INVÁLIDOS"
    return render_template("login.html", erro=erro)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    clientes = fetchall("SELECT * FROM clientes ORDER BY id DESC LIMIT 5")
    orcs = fetchall("""SELECT o.*, c.nome cliente, v.modelo carro, v.placa placa
                       FROM orcamentos o
                       LEFT JOIN clientes c ON c.id=o.cliente_id
                       LEFT JOIN veiculos v ON v.id=o.veiculo_id
                       ORDER BY o.id DESC LIMIT 8""")
    ym = datetime.now().strftime("%Y-%m")
    total_mes = fetchone("""SELECT COALESCE(SUM(valor),0) total FROM financeiro
                            WHERE tipo='ENTRADA' AND substr(data,1,7)=:ym""", {"ym": ym})["total"]
    pendente = fetchone("""SELECT COALESCE(SUM(valor),0) total FROM financeiro
                           WHERE tipo='ENTRADA' AND status='PENDENTE'""")["total"]
    return render_template("index.html", clientes=clientes, orcs=orcs, total_mes=total_mes, pendente=pendente)

@app.route("/clientes", methods=["GET","POST"])
@login_required
def clientes():
    if request.method == "POST":
        execute("""INSERT INTO clientes(nome, telefone, endereco, criado_em)
                   VALUES(:nome,:telefone,:endereco,:criado)""",
                {"nome": normalizar(request.form["nome"]), "telefone": request.form.get("telefone",""),
                 "endereco": normalizar(request.form.get("endereco","")),
                 "criado": datetime.now().strftime("%Y-%m-%d")})
        return redirect(url_for("clientes"))
    lista = fetchall("SELECT * FROM clientes ORDER BY nome")
    return render_template("clientes.html", clientes=lista)

@app.route("/clientes/excluir/<int:id>")
@login_required
def excluir_cliente(id):
    execute("DELETE FROM clientes WHERE id=:id", {"id": id})
    return redirect(url_for("clientes"))

@app.route("/veiculos", methods=["GET","POST"])
@login_required
def veiculos():
    if request.method == "POST":
        execute("""INSERT INTO veiculos(cliente_id,modelo,placa,ano,cor,km)
                   VALUES(:cliente_id,:modelo,:placa,:ano,:cor,:km)""",
                {"cliente_id": request.form["cliente_id"], "modelo": normalizar(request.form["modelo"]),
                 "placa": normalizar(request.form.get("placa","")).replace(" ",""),
                 "ano": normalizar(request.form.get("ano","")), "cor": normalizar(request.form.get("cor","")),
                 "km": normalizar(request.form.get("km",""))})
        return redirect(url_for("veiculos"))
    clientes = fetchall("SELECT * FROM clientes ORDER BY nome")
    lista = fetchall("""SELECT v.*, c.nome cliente FROM veiculos v
                        LEFT JOIN clientes c ON c.id=v.cliente_id ORDER BY v.id DESC""")
    return render_template("veiculos.html", veiculos=lista, clientes=clientes)

@app.route("/veiculos/excluir/<int:id>")
@login_required
def excluir_veiculo(id):
    execute("DELETE FROM veiculos WHERE id=:id", {"id": id})
    return redirect(url_for("veiculos"))

def recalcular_parcelas(orc_id):
    execute("DELETE FROM financeiro WHERE orcamento_id=:id", {"id": orc_id})
    o = fetchone("SELECT * FROM orcamentos WHERE id=:id", {"id": orc_id})
    if not o:
        return
    parcelas = max(1, int(o["parcelas"] or 1))
    valor_parcela = float(o["valor_parcela"] or o["total"] or 0)
    try:
        data_base = datetime.strptime(o["primeira_parcela"] or o["data"], "%Y-%m-%d")
    except Exception:
        data_base = datetime.now()
    for i in range(parcelas):
        venc = data_base + timedelta(days=30*i)
        execute("""INSERT INTO financeiro(data,tipo,descricao,valor,forma,orcamento_id,parcela_num,parcelas_total,status)
                   VALUES(:data,'ENTRADA',:descricao,:valor,:forma,:orc,:num,:total,'PENDENTE')""",
                {"data": venc.strftime("%Y-%m-%d"), "descricao": f"ORÇAMENTO #{orc_id} - PARCELA {i+1}/{parcelas}",
                 "valor": valor_parcela, "forma": o["pagamento"], "orc": orc_id, "num": i+1, "total": parcelas})


def salvar_itens_orcamento(orc_id, form):
    execute("DELETE FROM orcamento_itens WHERE orcamento_id=:id", {"id": orc_id})
    descs = form.getlist("item_desc[]")
    qtds = form.getlist("item_qtd[]")
    vals = form.getlist("item_valor[]")
    total_pecas = 0.0

    for desc, qtd, val in zip(descs, qtds, vals):
        desc = normalizar(desc)
        qtd = money(qtd)
        val = money(val)
        total = qtd * val
        if desc and total > 0:
            execute("""INSERT INTO orcamento_itens(orcamento_id,tipo,descricao,quantidade,valor_unitario,total)
                       VALUES(:orc,'PEÇA',:desc,:qtd,:val,:total)""",
                    {"orc": orc_id, "desc": desc, "qtd": qtd, "val": val, "total": total})
            total_pecas += total
    return total_pecas

def carregar_itens(orc_id):
    if not orc_id:
        return []
    return fetchall("SELECT * FROM orcamento_itens WHERE orcamento_id=:id ORDER BY id", {"id": orc_id})

def base_url():
    return request.url_root.rstrip("/")

@app.route("/orcamentos", methods=["GET","POST"])
@app.route("/orcamentos/editar/<int:edit_id>", methods=["GET","POST"])
@login_required
def orcamentos(edit_id=None):
    if request.method == "POST":
        mao = money(request.form.get("mao_obra_valor"))
        desconto = money(request.form.get("desconto"))
        acrescimo = money(request.form.get("acrescimo"))
        parcelas = max(1, int(request.form.get("parcelas") or 1))

        params = {
            "cliente_id": request.form["cliente_id"],
            "veiculo_id": request.form["veiculo_id"],
            "data": request.form.get("data") or datetime.now().strftime("%Y-%m-%d"),
            "status": normalizar(request.form.get("status","ABERTO")),
            "descricao": normalizar(request.form.get("descricao","")),
            "pecas_desc": "",
            "pecas_valor": 0,
            "mao_obra_desc": normalizar(request.form.get("mao_obra_desc","")),
            "mao_obra_valor": mao,
            "desconto": desconto,
            "acrescimo": acrescimo,
            "total": 0,
            "pagamento": normalizar(request.form.get("pagamento","NÃO INFORMADO")),
            "parcelas": parcelas,
            "valor_parcela": 0,
            "primeira_parcela": request.form.get("primeira_parcela") or datetime.now().strftime("%Y-%m-%d"),
            "observacoes": normalizar(request.form.get("observacoes","")),
        }

        if request.form.get("edit_id"):
            oid = int(request.form["edit_id"])
            params["id"] = oid
            execute("""UPDATE orcamentos SET cliente_id=:cliente_id, veiculo_id=:veiculo_id, data=:data, status=:status,
                       descricao=:descricao, pecas_desc=:pecas_desc, pecas_valor=:pecas_valor,
                       mao_obra_desc=:mao_obra_desc, mao_obra_valor=:mao_obra_valor,
                       desconto=:desconto, acrescimo=:acrescimo, total=:total, pagamento=:pagamento,
                       parcelas=:parcelas, valor_parcela=:valor_parcela, primeira_parcela=:primeira_parcela,
                       observacoes=:observacoes WHERE id=:id""", params)
        else:
            with engine.begin() as con:
                result = con.execute(text("""INSERT INTO orcamentos(cliente_id,veiculo_id,data,status,descricao,pecas_desc,pecas_valor,
                    mao_obra_desc,mao_obra_valor,desconto,acrescimo,total,pagamento,parcelas,valor_parcela,primeira_parcela,observacoes)
                    VALUES(:cliente_id,:veiculo_id,:data,:status,:descricao,:pecas_desc,:pecas_valor,:mao_obra_desc,:mao_obra_valor,
                    :desconto,:acrescimo,:total,:pagamento,:parcelas,:valor_parcela,:primeira_parcela,:observacoes) RETURNING id"""), params)
                oid = result.scalar()

        total_pecas = salvar_itens_orcamento(oid, request.form)
        total = total_pecas + mao - desconto + acrescimo
        valor_parcela = round(total / parcelas, 2)
        execute("""UPDATE orcamentos SET pecas_valor=:pecas, total=:total, valor_parcela=:vp WHERE id=:id""",
                {"pecas": total_pecas, "total": total, "vp": valor_parcela, "id": oid})

        recalcular_parcelas(oid)
        return redirect(url_for("orcamentos"))

    clientes = fetchall("SELECT * FROM clientes ORDER BY nome")
    veiculos = fetchall("""SELECT v.*, c.nome cliente FROM veiculos v
                           LEFT JOIN clientes c ON c.id=v.cliente_id ORDER BY c.nome, v.modelo""")
    lista = fetchall("""SELECT o.*, c.nome cliente, c.telefone telefone, v.modelo carro, v.placa placa
                        FROM orcamentos o
                        LEFT JOIN clientes c ON c.id=o.cliente_id
                        LEFT JOIN veiculos v ON v.id=o.veiculo_id
                        ORDER BY o.id DESC""")
    edit = fetchone("SELECT * FROM orcamentos WHERE id=:id", {"id": edit_id}) if edit_id else None
    itens_edit = carregar_itens(edit_id) if edit_id else []
    return render_template("orcamentos.html", clientes=clientes, veiculos=veiculos, orcamentos=lista, edit=edit, itens_edit=itens_edit)

@app.route("/orcamentos/excluir/<int:id>")
@login_required
def excluir_orcamento(id):
    execute("DELETE FROM financeiro WHERE orcamento_id=:id", {"id": id})
    execute("DELETE FROM orcamento_itens WHERE orcamento_id=:id", {"id": id})
    execute("DELETE FROM ordens_servico WHERE orcamento_id=:id", {"id": id})
    execute("DELETE FROM orcamentos WHERE id=:id", {"id": id})
    return redirect(url_for("orcamentos"))

def get_orcamento(id):
    return fetchone("""SELECT o.*, c.nome cliente, c.telefone telefone, c.endereco endereco,
                      v.modelo carro, v.placa placa, v.ano ano, v.cor cor
                      FROM orcamentos o
                      LEFT JOIN clientes c ON c.id=o.cliente_id
                      LEFT JOIN veiculos v ON v.id=o.veiculo_id
                      WHERE o.id=:id""", {"id": id})

@app.route("/orcamentos/pdf/<int:id>")
def pdf_orcamento(id):
    o = get_orcamento(id)
    if not o:
        return "ORÇAMENTO NÃO ENCONTRADO", 404

    try:
        itens = carregar_itens(id)
    except Exception:
        itens = []

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4

    def nova_pagina():
        c.setFillColor(colors.HexColor("#202428"))
        c.rect(0, h-4.4*cm, w, 4.4*cm, fill=1, stroke=0)

        c.setFillColor(colors.HexColor("#D4AF37"))
        c.roundRect(1.3*cm, h-3.35*cm, 2.2*cm, 2.2*cm, 12, fill=0, stroke=1)
        c.setFont("Helvetica-Bold", 24)
        c.drawCentredString(2.4*cm, h-2.6*cm, "A")

        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 19)
        c.drawString(4.0*cm, h-1.55*cm, "AUTO MECÂNICA ADEMAR")

        c.setFillColor(colors.HexColor("#E9D89B"))
        c.setFont("Helvetica-Bold", 11)
        c.drawString(4.0*cm, h-2.15*cm, "MECÂNICA EM GERAL")

        c.setFillColor(colors.HexColor("#D7D7D7"))
        c.setFont("Helvetica", 8.5)
        c.drawString(4.0*cm, h-2.72*cm, "RUA CONCEIÇÃO DA BARRA, 436 - SÃO SALVADOR")
        c.drawString(4.0*cm, h-3.22*cm, "FIXO: 3477-7455 | WHATSAPP: (31) 98801-7455")

        c.setFillColor(colors.HexColor("#D4AF37"))
        c.roundRect(w-5.5*cm, h-2.95*cm, 4.2*cm, 1.25*cm, 10, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#202428"))
        c.setFont("Helvetica-Bold", 13)
        c.drawCentredString(w-3.4*cm, h-2.48*cm, f"ORÇAMENTO #{id}")

        return h - 5.2*cm

    def secao(titulo, y):
        c.setFillColor(colors.HexColor("#F3EBDD"))
        c.roundRect(1.5*cm, y-0.2*cm, w-3.0*cm, 0.7*cm, 7, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#202428"))
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(1.9*cm, y, titulo)
        return y - 0.85*cm

    def rodape():
        c.setFillColor(colors.HexColor("#777777"))
        c.setFont("Helvetica", 7.5)
        c.drawCentredString(w/2, 1.15*cm, "AUTO MECÂNICA ADEMAR • MECÂNICA EM GERAL • CUIDAMOS DO SEU CARRO COMO SE FOSSE NOSSO")
        c.setFillColor(colors.HexColor("#D4AF37"))
        c.line(1.5*cm, 1.55*cm, w-1.5*cm, 1.55*cm)

    y = nova_pagina()

    # Cliente e veículo
    y = secao("DADOS DO CLIENTE E VEÍCULO", y)
    c.setFillColor(colors.HexColor("#333333"))
    c.setFont("Helvetica", 9.5)
    linhas_cliente = [
        f"CLIENTE: {o['cliente']}",
        f"TELEFONE / WHATSAPP: {o['telefone']}",
        f"VEÍCULO: {o['carro']} | PLACA: {o['placa']} | ANO: {o['ano']} | COR: {o['cor']}",
        f"DATA: {o['data']} | STATUS: {o['status']}",
    ]
    for linha in linhas_cliente:
        c.drawString(1.9*cm, y, linha[:120])
        y -= 0.48*cm

    y -= 0.25*cm

    # Peças
    y = secao("PEÇAS UTILIZADAS", y)
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColor(colors.HexColor("#202428"))
    c.drawString(1.9*cm, y, "DESCRIÇÃO")
    c.drawRightString(12.8*cm, y, "QTD")
    c.drawRightString(15.5*cm, y, "UNITÁRIO")
    c.drawRightString(19.1*cm, y, "TOTAL")
    y -= 0.35*cm
    c.setFillColor(colors.HexColor("#D4AF37"))
    c.line(1.9*cm, y, 19.1*cm, y)
    y -= 0.35*cm

    c.setFont("Helvetica", 8.5)
    c.setFillColor(colors.HexColor("#333333"))
    if itens:
        for item in itens:
            if y < 3.5*cm:
                rodape()
                c.showPage()
                y = nova_pagina()
            c.drawString(1.9*cm, y, str(item["descricao"])[:58])
            c.drawRightString(12.8*cm, y, str(item["quantidade"]))
            c.drawRightString(15.5*cm, y, brl(item["valor_unitario"]))
            c.drawRightString(19.1*cm, y, brl(item["total"]))
            y -= 0.42*cm
    else:
        c.drawString(1.9*cm, y, "SEM PEÇAS INFORMADAS")
        y -= 0.42*cm

    y -= 0.25*cm

    # Mão de obra
    y = secao("MÃO DE OBRA / SERVIÇOS", y)
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#333333"))
    mao_desc = (o["mao_obra_desc"] or "-").split("\n")
    for linha in mao_desc:
        if y < 3.5*cm:
            rodape()
            c.showPage()
            y = nova_pagina()
        c.drawString(1.9*cm, y, linha[:105])
        y -= 0.4*cm
    y -= 0.2*cm

    # Resumo financeiro
    y = secao("RESUMO FINANCEIRO", y)
    resumo = [
        ("TOTAL DE PEÇAS", o["pecas_valor"]),
        ("MÃO DE OBRA", o["mao_obra_valor"]),
        ("DESCONTO", o["desconto"]),
        ("ACRÉSCIMO", o["acrescimo"]),
    ]
    c.setFont("Helvetica", 9.5)
    for nome, valor in resumo:
        c.setFillColor(colors.HexColor("#333333"))
        c.drawString(11.2*cm, y, nome)
        c.drawRightString(19.1*cm, y, brl(valor))
        y -= 0.45*cm

    c.setFillColor(colors.HexColor("#202428"))
    c.roundRect(10.8*cm, y-0.35*cm, 8.5*cm, 0.9*cm, 8, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#D4AF37"))
    c.setFont("Helvetica-Bold", 14)
    c.drawString(11.2*cm, y, "TOTAL GERAL")
    c.drawRightString(18.9*cm, y, brl(o["total"]))
    y -= 1.2*cm

    # Pagamento
    y = secao("CONDIÇÃO DE PAGAMENTO", y)
    c.setFillColor(colors.HexColor("#333333"))
    c.setFont("Helvetica", 9.5)
    c.drawString(1.9*cm, y, f"FORMA DE PAGAMENTO: {o['pagamento']}")
    y -= 0.45*cm
    c.drawString(1.9*cm, y, f"PARCELAMENTO: {o['parcelas']}x DE {brl(o['valor_parcela'])}")
    y -= 0.45*cm
    c.drawString(1.9*cm, y, f"PRIMEIRA PARCELA: {o['primeira_parcela']}")
    y -= 0.7*cm

    # Observações
    y = secao("OBSERVAÇÕES", y)
    c.setFont("Helvetica", 8.5)
    obs = o["observacoes"] or "ORÇAMENTO SUJEITO À APROVAÇÃO DO CLIENTE."
    c.drawString(1.9*cm, y, obs[:120])
    y -= 0.8*cm

    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(colors.HexColor("#666666"))
    c.drawString(1.9*cm, y, "Este orçamento foi elaborado com base nas informações e condições verificadas no momento da avaliação.")
    y -= 0.35*cm
    c.drawString(1.9*cm, y, "A execução dos serviços depende da aprovação do cliente.")

    rodape()
    c.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=False, download_name=f"ORCAMENTO_{id}_AUTO_MECANICA_ADEMAR.pdf", mimetype="application/pdf")

@app.route("/orcamentos/whatsapp/<int:id>")
@login_required
def whatsapp(id):
    o = get_orcamento(id)
    if not o:
        return redirect(url_for("orcamentos"))

    numero = "".join(ch for ch in (o["telefone"] or "") if ch.isdigit())
    pdf_link = f"{request.url_root.rstrip('/')}/orcamentos/pdf/{id}"

    msg = (
        f"Olá, {o['cliente']}! 👋\n\n"
        f"Aqui é da *Auto Mecânica Ademar* 🔧🚗\n\n"
        f"Preparamos o orçamento do seu veículo com atenção e transparência:\n\n"
        f"🚘 *Veículo:* {o['carro']} - placa {o['placa']}\n\n"
        f"💰 *Valor total:* {brl(o['total'])}\n"
        f"💳 *Forma de pagamento:* {o['pagamento']}\n"
        f"📊 *Parcelamento:* {o['parcelas']}x de {brl(o['valor_parcela'])}\n\n"
        f"📄 *Orçamento completo em PDF:*\n{pdf_link}\n\n"
        f"Para aprovar o serviço, basta responder esta mensagem confirmando. 👍\n\n"
        f"Se tiver qualquer dúvida, estamos à disposição para explicar cada item.\n\n"
        f"Agradecemos pela confiança! 🤝\n\n"
        f"*Auto Mecânica Ademar*\n"
        f"_Mecânica em geral_"
    )

    return redirect("https://api.whatsapp.com/send?phone=55" + numero + "&text=" + urllib.parse.quote(msg, safe=""))



@app.route("/ordens-servico", methods=["GET","POST"])
@login_required
def ordens_servico():
    if request.method == "POST":
        orc_id = request.form.get("orcamento_id") or None
        orc = fetchone("SELECT * FROM orcamentos WHERE id=:id", {"id": orc_id}) if orc_id else None
        cliente_id = orc["cliente_id"] if orc else request.form.get("cliente_id")
        veiculo_id = orc["veiculo_id"] if orc else request.form.get("veiculo_id")

        execute("""INSERT INTO ordens_servico(orcamento_id,cliente_id,veiculo_id,data_abertura,data_fechamento,status,descricao,observacoes)
                   VALUES(:orc,:cli,:vei,:abertura,:fechamento,:status,:descricao,:obs)""",
                {"orc": orc_id, "cli": cliente_id, "vei": veiculo_id,
                 "abertura": request.form.get("data_abertura") or datetime.now().strftime("%Y-%m-%d"),
                 "fechamento": request.form.get("data_fechamento") or "",
                 "status": normalizar(request.form.get("status","EM ANDAMENTO")),
                 "descricao": normalizar(request.form.get("descricao","")),
                 "obs": normalizar(request.form.get("observacoes",""))})
        return redirect(url_for("ordens_servico"))

    clientes = fetchall("SELECT * FROM clientes ORDER BY nome")
    veiculos = fetchall("""SELECT v.*, c.nome cliente FROM veiculos v LEFT JOIN clientes c ON c.id=v.cliente_id ORDER BY c.nome""")
    orcamentos_lista = fetchall("""SELECT o.*, c.nome cliente, v.modelo carro, v.placa placa FROM orcamentos o
                                  LEFT JOIN clientes c ON c.id=o.cliente_id
                                  LEFT JOIN veiculos v ON v.id=o.veiculo_id
                                  ORDER BY o.id DESC LIMIT 200""")
    lista = fetchall("""SELECT os.*, c.nome cliente, v.modelo carro, v.placa placa
                        FROM ordens_servico os
                        LEFT JOIN clientes c ON c.id=os.cliente_id
                        LEFT JOIN veiculos v ON v.id=os.veiculo_id
                        ORDER BY os.id DESC""")
    return render_template("ordens_servico.html", lista=lista, clientes=clientes, veiculos=veiculos, orcamentos=orcamentos_lista)

@app.route("/ordens-servico/excluir/<int:id>")
@login_required
def excluir_os(id):
    execute("DELETE FROM ordens_servico WHERE id=:id", {"id": id})
    return redirect(url_for("ordens_servico"))

@app.route("/ordens-servico/status/<int:id>/<status>")
@login_required
def status_os(id, status):
    execute("UPDATE ordens_servico SET status=:status WHERE id=:id", {"status": normalizar(status), "id": id})
    return redirect(url_for("ordens_servico"))


@app.route("/financeiro", methods=["GET","POST"])
@login_required
def financeiro():
    if request.method == "POST":
        execute("""INSERT INTO financeiro(data,tipo,descricao,valor,forma,status)
                   VALUES(:data,:tipo,:descricao,:valor,:forma,:status)""",
                {"data": request.form["data"], "tipo": normalizar(request.form["tipo"]),
                 "descricao": normalizar(request.form["descricao"]), "valor": money(request.form["valor"]),
                 "forma": normalizar(request.form["forma"]), "status": normalizar(request.form["status"])})
        return redirect(url_for("financeiro"))
    itens = fetchall("SELECT * FROM financeiro ORDER BY data DESC, id DESC LIMIT 300")
    return render_template("financeiro.html", itens=itens)

@app.route("/financeiro/pago/<int:id>")
@login_required
def financeiro_pago(id):
    execute("UPDATE financeiro SET status='PAGO' WHERE id=:id", {"id": id})
    return redirect(url_for("financeiro"))

@app.route("/financeiro/excluir/<int:id>")
@login_required
def excluir_financeiro(id):
    execute("DELETE FROM financeiro WHERE id=:id", {"id": id})
    return redirect(url_for("financeiro"))

@app.route("/relatorios")
@login_required
def relatorios():
    ano = request.args.get("ano") or datetime.now().strftime("%Y")
    mes = request.args.get("mes") or datetime.now().strftime("%m")
    ym = f"{ano}-{mes}"
    mensal = fetchall("""SELECT forma, status, COUNT(*) qtd, COALESCE(SUM(valor),0) total
                         FROM financeiro WHERE substr(data,1,7)=:ym AND tipo='ENTRADA'
                         GROUP BY forma,status ORDER BY forma,status""", {"ym": ym})
    saidas = fetchone("""SELECT COALESCE(SUM(valor),0) total FROM financeiro
                         WHERE substr(data,1,7)=:ym AND tipo='SAÍDA'""", {"ym": ym})["total"]
    entradas = fetchone("""SELECT COALESCE(SUM(valor),0) total FROM financeiro
                           WHERE substr(data,1,7)=:ym AND tipo='ENTRADA' AND status='PAGO'""", {"ym": ym})["total"]
    anual = fetchall("""SELECT substr(data,1,7) mes,
                        COALESCE(SUM(CASE WHEN tipo='ENTRADA' THEN valor ELSE -valor END),0) saldo
                        FROM financeiro WHERE substr(data,1,4)=:ano
                        GROUP BY substr(data,1,7) ORDER BY mes""", {"ano": ano})
    parcelas = fetchall("""SELECT * FROM financeiro WHERE tipo='ENTRADA' AND status='PENDENTE'
                           ORDER BY data ASC LIMIT 100""")
    chart_labels = [r["mes"] for r in anual]
    chart_values = [float(r["saldo"] or 0) for r in anual]
    formas_labels = [str(r["forma"]) + " " + str(r["status"]) for r in mensal]
    formas_values = [float(r["total"] or 0) for r in mensal]
    return render_template("relatorios.html", mensal=mensal, anual=anual, parcelas=parcelas,
                           entradas=entradas, saidas=saidas, lucro=entradas-saidas, ano=ano, mes=mes,
                           chart_labels=chart_labels, chart_values=chart_values,
                           formas_labels=formas_labels, formas_values=formas_values)

if __name__ == "__main__":
    app.run(debug=True)
