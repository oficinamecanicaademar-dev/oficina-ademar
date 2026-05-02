
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
        execute("""INSERT INTO veiculos(cliente_id,modelo,placa,ano,cor)
                   VALUES(:cliente_id,:modelo,:placa,:ano,:cor)""",
                {"cliente_id": request.form["cliente_id"], "modelo": normalizar(request.form["modelo"]),
                 "placa": normalizar(request.form.get("placa","")).replace(" ",""),
                 "ano": normalizar(request.form.get("ano","")), "cor": normalizar(request.form.get("cor",""))})
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
@login_required
def pdf_orcamento(id):
    o = get_orcamento(id)
    if not o:
        return "ORÇAMENTO NÃO ENCONTRADO", 404
    itens = carregar_itens(id)
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w,h = A4
    c.setFillColor(colors.HexColor("#252B30"))
    c.rect(0, h-4.2*cm, w, 4.2*cm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(2*cm, h-1.5*cm, "AUTO MECÂNICA ADEMAR")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2*cm, h-2.2*cm, "MECÂNICA EM GERAL")
    c.setFont("Helvetica", 9)
    c.drawString(2*cm, h-2.8*cm, "RUA CONCEIÇÃO DA BARRA, 436 - SÃO SALVADOR")
    c.drawString(2*cm, h-3.3*cm, "FIXO: 3477-7455 | WHATSAPP: (31) 98801-7455")
    c.setFillColor(colors.HexColor("#A94343"))
    c.roundRect(w-5.2*cm, h-2.8*cm, 3.8*cm, 1.1*cm, 8, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(w-3.3*cm, h-2.4*cm, f"ORÇAMENTO #{id}")

    y = h-5.1*cm
    def section(title):
        nonlocal y
        c.setFillColor(colors.HexColor("#F0E8E8"))
        c.roundRect(1.6*cm, y-0.2*cm, w-3.2*cm, 0.75*cm, 6, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#333333"))
        c.setFont("Helvetica-Bold", 11)
        c.drawString(2*cm, y, title)
        y -= 0.85*cm

    section("CLIENTE E VEÍCULO")
    c.setFont("Helvetica", 10)
    c.setFillColor(colors.HexColor("#333333"))
    for line in [
        f"CLIENTE: {o['cliente']}",
        f"TELEFONE: {o['telefone']}",
        f"VEÍCULO: {o['carro']} | PLACA: {o['placa']} | ANO: {o['ano']} | COR: {o['cor']}",
        f"DATA: {o['data']} | STATUS: {o['status']}",
    ]:
        c.drawString(2*cm, y, line[:115])
        y -= 0.48*cm
    y -= 0.2*cm

    section("PEÇAS E MÃO DE OBRA")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(2*cm, y, "PEÇAS")
    c.drawRightString(w-2*cm, y, brl(o["pecas_valor"]))
    y -= 0.45*cm
    c.setFont("Helvetica", 9)
    if itens:
        for item in itens:
            linha = f"{item['descricao']} | QTD: {item['quantidade']} | UNIT.: {brl(item['valor_unitario'])} | TOTAL: {brl(item['total'])}"
            c.drawString(2.2*cm, y, linha[:115])
            y -= 0.35*cm
            if y < 4*cm:
                c.showPage()
                y = h-2*cm
    else:
        c.drawString(2.2*cm, y, "-")
        y -= 0.35*cm
    y -= 0.25*cm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(2*cm, y, "MÃO DE OBRA")
    c.drawRightString(w-2*cm, y, brl(o["mao_obra_valor"]))
    y -= 0.45*cm
    c.setFont("Helvetica", 9)
    for line in (o["mao_obra_desc"] or "-").split("\n"):
        c.drawString(2.2*cm, y, line[:100])
        y -= 0.35*cm
    y -= 0.25*cm

    section("PAGAMENTO E TOTAL")
    c.setFont("Helvetica", 10)
    for line in [
        f"DESCONTO: {brl(o['desconto'])}",
        f"ACRÉSCIMO: {brl(o['acrescimo'])}",
        f"PAGAMENTO: {o['pagamento']} | PARCELAS: {o['parcelas']}x DE {brl(o['valor_parcela'])}",
        f"PRIMEIRA PARCELA: {o['primeira_parcela']}",
    ]:
        c.drawString(2*cm, y, line)
        y -= 0.45*cm
    c.setFillColor(colors.HexColor("#A94343"))
    c.setFont("Helvetica-Bold", 17)
    c.drawRightString(w-2*cm, y, f"TOTAL: {brl(o['total'])}")
    y -= 0.9*cm

    c.setFillColor(colors.HexColor("#333333"))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(2*cm, y, "OBSERVAÇÕES")
    y -= 0.4*cm
    c.setFont("Helvetica", 9)
    c.drawString(2*cm, y, (o["observacoes"] or "-")[:105])

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#777777"))
    c.drawCentredString(w/2, 1.2*cm, "AUTO MECÂNICA ADEMAR • MECÂNICA EM GERAL • OBRIGADO PELA CONFIANÇA")
    c.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=False, download_name=f"ORCAMENTO_{id}.pdf", mimetype="application/pdf")

@app.route("/orcamentos/whatsapp/<int:id>")
@login_required
def whatsapp(id):
    o = get_orcamento(id)
    if not o:
        return redirect(url_for("orcamentos"))

    numero = "".join(ch for ch in (o["telefone"] or "") if ch.isdigit())
    pdf_link = f"{base_url()}/orcamentos/pdf/{id}"

    msg = f"""Olá, {o['cliente']}! 👋

Aqui é da *Auto Mecânica Ademar* 🔧🚗

Preparamos o orçamento do seu veículo:

🚘 *Veículo:* {o['carro']} - {o['placa']}

💰 *Valor total:* {brl(o['total'])}
💳 *Forma de pagamento:* {o['pagamento']}
📊 *Parcelamento:* {o['parcelas']}x de {brl(o['valor_parcela'])}

📄 *Orçamento completo em PDF:*
{pdf_link}

Caso queira aprovar o serviço, é só responder esta mensagem 👍

Agradecemos pela confiança! 🤝
*Auto Mecânica Ademar*
_Mecânica em geral_"""

    return redirect("https://wa.me/55" + numero + "?text=" + urllib.parse.quote(msg, safe=""))


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
