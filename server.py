#!/usr/bin/env python3
"""
Sistema de Controle de Ouvidoria  v3.0
Backend: Python stdlib HTTP + SQLite
Autenticação por sessão (token) + controle de usuários
"""

import sqlite3, json, os, re, io, csv as csv_mod, hashlib, secrets, traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import date, datetime, timedelta

DB_PATH   = os.path.join(os.path.dirname(__file__), "ouvidoria.db")
DATA_PATH = os.path.join(os.path.dirname(__file__), "data_seed.json")
PORT      = 8765
SESSION_HOURS = 8   # sessão expira após 8h de inatividade

# ── BANCO ─────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def hash_senha(senha):
    return hashlib.sha256(senha.encode('utf-8')).hexdigest()

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS processos (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        frente                  TEXT DEFAULT 'OUVIDORIA',
        numero_processo         TEXT NOT NULL,
        entrada                 TEXT,
        data_entrada            TEXT,
        prazo_final             TEXT,
        modalidade              TEXT,
        tema                    TEXT,
        assunto                 TEXT,
        descritivo              TEXT,
        retorno                 TEXT,
        envolvidos              TEXT,
        local                   TEXT,
        sensibilidade           TEXT,
        servidor_atribuido      TEXT,
        status                  TEXT DEFAULT 'EM ANÁLISE',
        tempo_medio_atendimento TEXT,
        inicio_tratamento       TEXT,
        sintese                 TEXT,
        nome_envolvidos         TEXT,
        nome_local              TEXT,
        pendencias_area         TEXT,
        sintese_parecer         TEXT,
        data_finalizacao        TEXT,
        unidade_envio           TEXT,
        data_envio              TEXT,
        prazo_devolucao         TEXT,
        data_devolucao          TEXT,
        situacao_final          TEXT,
        observacoes             TEXT,
        criado_em               TEXT DEFAULT (datetime('now','localtime')),
        atualizado_em           TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_numero   ON processos(numero_processo);
    CREATE INDEX IF NOT EXISTS idx_status   ON processos(status);
    CREATE INDEX IF NOT EXISTS idx_tema     ON processos(tema);
    CREATE INDEX IF NOT EXISTS idx_servidor ON processos(servidor_atribuido);
    CREATE INDEX IF NOT EXISTS idx_sensib   ON processos(sensibilidade);
    CREATE INDEX IF NOT EXISTS idx_entrada  ON processos(data_entrada);

    CREATE TABLE IF NOT EXISTS categorias (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo   TEXT NOT NULL,
        valor  TEXT NOT NULL,
        pai_id INTEGER REFERENCES categorias(id),
        ativo  INTEGER DEFAULT 1,
        UNIQUE(tipo, valor, pai_id)
    );

    CREATE TABLE IF NOT EXISTS log_alteracoes (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        processo_id  INTEGER,
        acao         TEXT DEFAULT 'edição',
        campo        TEXT,
        valor_antes  TEXT,
        valor_depois TEXT,
        usuario_id   INTEGER REFERENCES usuarios(id),
        usuario_nome TEXT,
        momento      TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS usuarios (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        nome       TEXT NOT NULL,
        login      TEXT NOT NULL UNIQUE,
        senha_hash TEXT NOT NULL,
        perfil     TEXT DEFAULT 'operador',
        ativo      INTEGER DEFAULT 1,
        criado_em  TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS sessoes (
        token      TEXT PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
        expira_em  TEXT NOT NULL,
        criado_em  TEXT DEFAULT (datetime('now','localtime'))
    );
    """)
    conn.commit()

    # Usuário admin padrão (login: admin  /  senha: ouvidoria2026)
    existe = conn.execute("SELECT id FROM usuarios WHERE login='admin'").fetchone()
    if not existe:
        conn.execute(
            "INSERT INTO usuarios(nome,login,senha_hash,perfil) VALUES(?,?,?,?)",
            ('Administrador', 'admin', hash_senha('ouvidoria2026'), 'admin')
        )
        conn.commit()
        print("[AUTH] Usuário admin criado. Login: admin / Senha: ouvidoria2026")

    seed_categorias(conn)
    count = conn.execute("SELECT COUNT(*) FROM processos").fetchone()[0]
    if count == 0 and os.path.exists(DATA_PATH):
        seed_data(conn)
    conn.close()

# ── SEED CATEGORIAS ───────────────────────────────────────────────────────────

def seed_categorias(conn):
    def ins(tipo, valor, pai_id=None):
        try: conn.execute("INSERT OR IGNORE INTO categorias(tipo,valor,pai_id) VALUES(?,?,?)", (tipo, valor, pai_id))
        except: pass
    def get_id(tipo, valor):
        r = conn.execute("SELECT id FROM categorias WHERE tipo=? AND valor=?", (tipo, valor)).fetchone()
        return r['id'] if r else None

    for m in ['Denúncia','Reclamação','Elogio','Solicitação','Sugestão','Indeferimento']:
        ins('modalidade', m)
    temas = ['Alimentação escolar','Avaliações educacionais','CEU (todos)','CEU PMSP',
             'CEU Tercerizado','Cargos','Cesta básica','Concurso','Contratos','Currículo',
             'Determinação Judicial','Documentação','Educação Especial','Estudantes',
             'Indeferimento','Leve Leite','Material escolar','Matrícula/Demanda',
             'Má conduta de servidores','Mães guardiãs','Orçamento','Parcerias',
             'Predial','Programas','Qualidade no atendimento','Recreio nas férias',
             'SME/DRE','Segurança','TEG','Transferência','Unidades educacionais','Violência']
    for t in temas: ins('tema', t)
    assuntos_por_tema = {
        'Má conduta de servidores': ['Agressão','Assédio moral','Assédio sexual','Descumprimento de legislação','Financeiro','Omissão','Bullying'],
        'Transferência':            ['Procedimentos','Quantitativos'],
        'Qualidade no atendimento': ['Circulação','Descumprimento de horário','Desvio de função','Falta de profissionais','Falta de urbanidade','Materiais','Morosidade','Falha de comunicação'],
        'Unidades educacionais':    ['Aulas','Contato','Documentação','Lista com unidades','Quantitativos'],
        'TEG':                      ['Critérios','Não atendimento','Orçamento','Quantitativos'],
        'Leve Leite':               ['Calendário','Contratos','Critérios','Não recebimento','Orçamento'],
        'Matrícula/Demanda':        ['Procedimentos','Quantitativos','Projeção de Nova UE'],
        'Alimentação escolar':      ['Restrição alimentar','Qualidade','Quantidade','Cardápio'],
        'Educação Especial':        ['Atendimento','Legislação','Orçamento','Procedimento','Projetos'],
        'Predial':                  ['Acessibilidade','Construção','Documentação','Falta de água','Interdição','Limpeza','Reforma','Zeladoria'],
        'Cargos':                   ['Efetivos','Comissionados','Evolução funcional'],
        'Cesta básica':             ['Critérios','Qualidade dos produtos','Quantitativos'],
        'CEU PMSP':                 ['Atividades complementares'],
        'Parcerias':                ['Financeiro','Contratos'],
        'Contratos':                ['Termos','Lista'],
    }
    for tema, assuntos in assuntos_por_tema.items():
        tid = get_id('tema', tema)
        for a in assuntos: ins('assunto', a, tid)
    descritivos_por_assunto = {
        'Descumprimento de legislação': ['Estatuto do Servidor','LGPD','Acúmulo indevido','Racismo','Gestão','Readaptados','Comércio irregular'],
        'Não atendimento':              ['Faz jus','Não faz jus'],
        'Procedimentos':                ['Vaga específica','Vaga não específica','Como solicitar o benefício'],
        'Agressão':                     ['Entre alunos','Entre servidores','Servidor-Aluno'],
        'Assédio moral':                ['Entre servidores','Com o munícipe'],
        'Assédio sexual':               ['Com o munícipe -18','Com o munícipe +18'],
        'Não recebimento':              ['Ciclo','Entrega','CadÚnico','Cadastro EOL','Não faz jus'],
        'Aulas':                        ['Regulares','Greve Servidores','Greve Terceirizado'],
    }
    for assunto, descs in descritivos_por_assunto.items():
        aid = get_id('assunto', assunto)
        if aid:
            for d in descs: ins('descritivo', d, aid)
    for u in ['COGED','COPED','CODAE','COCEU','COEB','CONAE','CEFAI','GABINETE',
              'DRE-BT','DRE-CL','DRE-CS','DRE-FO','DRE-FB','DRE-G','DRE-IP',
              'DRE-IQ','DRE-JT','DRE-MP','DRE-PE','DRE-PJ','DRE-SA','DRE-SM','DRE-MO']:
        ins('unidade_envio', u)
    for s in ['CLÉO','ERIKA','LUCI','MARI','THAYNAN']: ins('servidor', s)
    for l in ['CEI DIRETO','CEI INDIRETO','CEMEI','CEU DIRETO','CEU INDIRETO',
              'CIEJA','CR.P. CONV.','DRE','EMEBS','EMEF','EMEFM','EMEI','SME']:
        ins('local', l)
    for e in ['ALUNO','COLABORADOR','COLABORADOR OSC-ALUNO','COLABORADOR OSC-MUNÍCIPE',
              'COMISSIONADO','ENTRE ALUNOS','ENTRE SERVIDORES','MUNÍCIPE','MUNÍCIPE-ALUNO',
              'SERVIDOR','SERVIDOR-ALUNO','SERVIDOR-COLABORADOR','SERVIDOR-MUNÍCIPE','TERCEIRIZADO']:
        ins('envolvidos', e)
    for en in ['CGM/OGM','CGM/OGM/NAD','CGM/OGM/NASD','Desdobramento','Gabinete','Outros','Reabertura']:
        ins('entrada', en)
    for st in ['EM ANÁLISE','EM TRAMITAÇÃO','PRORROGADO','FINALIZADO']: ins('status', st)
    for sb in ['ALTA','MÉDIA','BAIXA']: ins('sensibilidade', sb)
    for sf in ['Prazo 20 dias','Prorrogados','Atrasados',
               'finalizado no prazo(até 20 dias)','finalizado na prorrogação(até 40)','finalizado com atraso(+40)']:
        ins('situacao_final', sf)
    conn.commit()

def seed_data(conn):
    with open(DATA_PATH, 'r', encoding='utf-8') as f:
        records = json.load(f)
    for r in records:
        try:
            conn.execute("""
                INSERT INTO processos (frente,numero_processo,entrada,data_entrada,prazo_final,
                    modalidade,tema,assunto,descritivo,retorno,envolvidos,local,sensibilidade,
                    servidor_atribuido,status,tempo_medio_atendimento,inicio_tratamento,sintese,
                    nome_envolvidos,nome_local,pendencias_area,sintese_parecer,data_finalizacao,
                    unidade_envio,data_envio,prazo_devolucao,data_devolucao,situacao_final)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (r.get('frente','OUVIDORIA'),r.get('numero_processo',''),r.get('entrada',''),
                  r.get('data_entrada'),r.get('prazo_final'),r.get('modalidade',''),r.get('tema',''),
                  r.get('assunto',''),r.get('descritivo',''),r.get('retorno',''),r.get('envolvidos',''),
                  r.get('local',''),r.get('sensibilidade',''),r.get('servidor_atribuido',''),
                  r.get('status','EM TRAMITAÇÃO'),r.get('tempo_medio_atendimento',''),
                  r.get('inicio_tratamento'),r.get('sintese',''),r.get('nome_envolvidos',''),
                  r.get('nome_local',''),r.get('pendencias_area',''),r.get('sintese_parecer',''),
                  r.get('data_finalizacao'),r.get('unidade_envio',''),r.get('data_envio'),
                  r.get('prazo_devolucao'),r.get('data_devolucao'),r.get('situacao_final','')))
        except: pass
    conn.commit()
    print(f"[SEED] {len(records)} registros importados.")

# ── HELPERS ───────────────────────────────────────────────────────────────────

def rows_to_list(rows): return [dict(r) for r in rows]

def json_response(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', len(body))
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.end_headers()
    handler.wfile.write(body)

def error_response(handler, msg, status=400):
    json_response(handler, {'error': msg}, status)

def read_body(handler):
    length = int(handler.headers.get('Content-Length', 0))
    return json.loads(handler.rfile.read(length)) if length else {}

# ── AUTENTICAÇÃO ──────────────────────────────────────────────────────────────

def get_token(handler):
    """Extrai o token do cabeçalho Authorization: Bearer <token>"""
    auth = handler.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:].strip()
    return None

def get_usuario_from_token(token):
    """Valida o token e retorna o usuário; None se inválido/expirado."""
    if not token: return None
    conn = get_db()
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    row  = conn.execute("""
        SELECT u.id, u.nome, u.login, u.perfil
        FROM sessoes s JOIN usuarios u ON s.usuario_id = u.id
        WHERE s.token=? AND s.expira_em > ? AND u.ativo=1
    """, (token, now)).fetchone()
    if row:
        # renova expiração a cada request (sessão deslizante)
        nova_exp = (datetime.now() + timedelta(hours=SESSION_HOURS)).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("UPDATE sessoes SET expira_em=? WHERE token=?", (nova_exp, token))
        conn.commit()
    conn.close()
    return dict(row) if row else None

def require_auth(handler):
    """Retorna o usuário autenticado ou envia 401 e retorna None."""
    token   = get_token(handler)
    usuario = get_usuario_from_token(token)
    if not usuario:
        error_response(handler, 'Não autenticado', 401)
    return usuario

def require_admin(handler):
    usuario = require_auth(handler)
    if not usuario: return None
    if usuario['perfil'] != 'admin':
        error_response(handler, 'Acesso restrito ao administrador', 403)
        return None
    return usuario

# ── ROTAS AUTH ────────────────────────────────────────────────────────────────

def handle_login(handler):
    data  = read_body(handler)
    login = (data.get('login') or '').strip()
    senha = (data.get('senha') or '').strip()
    if not login or not senha:
        return error_response(handler, 'Login e senha são obrigatórios')
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM usuarios WHERE login=? AND senha_hash=? AND ativo=1",
        (login, hash_senha(senha))
    ).fetchone()
    if not user:
        conn.close()
        return error_response(handler, 'Login ou senha inválidos', 401)
    # Cria token de sessão
    token    = secrets.token_hex(32)
    expira   = (datetime.now() + timedelta(hours=SESSION_HOURS)).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("INSERT INTO sessoes(token,usuario_id,expira_em) VALUES(?,?,?)", (token, user['id'], expira))
    conn.commit()
    conn.close()
    json_response(handler, {
        'token':  token,
        'nome':   user['nome'],
        'login':  user['login'],
        'perfil': user['perfil'],
        'expira': expira,
    })

def handle_logout(handler):
    token = get_token(handler)
    if token:
        conn = get_db()
        conn.execute("DELETE FROM sessoes WHERE token=?", (token,))
        conn.commit()
        conn.close()
    json_response(handler, {'ok': True})

def handle_me(handler):
    usuario = require_auth(handler)
    if not usuario: return
    json_response(handler, usuario)

# ── ROTAS USUÁRIOS ────────────────────────────────────────────────────────────

def handle_get_usuarios(handler):
    usuario = require_admin(handler)
    if not usuario: return
    conn = get_db()
    rows = rows_to_list(conn.execute(
        "SELECT id, nome, login, perfil, ativo, criado_em FROM usuarios ORDER BY nome"
    ).fetchall())
    conn.close()
    json_response(handler, rows)

def handle_create_usuario(handler):
    admin = require_admin(handler)
    if not admin: return
    data  = read_body(handler)
    nome  = (data.get('nome')  or '').strip()
    login = (data.get('login') or '').strip()
    senha = (data.get('senha') or '').strip()
    perfil = data.get('perfil', 'operador')
    if not nome or not login or not senha:
        return error_response(handler, 'nome, login e senha são obrigatórios')
    if perfil not in ('admin', 'operador'):
        return error_response(handler, 'perfil inválido')
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO usuarios(nome,login,senha_hash,perfil) VALUES(?,?,?,?)",
            (nome, login, hash_senha(senha), perfil)
        )
        conn.commit()
        row = conn.execute("SELECT id,nome,login,perfil,ativo,criado_em FROM usuarios WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
        json_response(handler, dict(row), 201)
    except sqlite3.IntegrityError:
        conn.close()
        error_response(handler, f'Login "{login}" já está em uso', 409)

def handle_update_usuario(handler, uid):
    admin = require_admin(handler)
    if not admin: return
    data  = read_body(handler)
    conn  = get_db()
    user  = conn.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    if not user: conn.close(); return error_response(handler, 'Usuário não encontrado', 404)
    updates = {}
    if 'nome'   in data and data['nome'].strip():  updates['nome']   = data['nome'].strip()
    if 'perfil' in data and data['perfil'] in ('admin','operador'): updates['perfil'] = data['perfil']
    if 'ativo'  in data: updates['ativo'] = 1 if data['ativo'] else 0
    if 'senha'  in data and data['senha'].strip(): updates['senha_hash'] = hash_senha(data['senha'].strip())
    if not updates: conn.close(); return error_response(handler, 'Nenhum campo para atualizar')
    conn.execute(f"UPDATE usuarios SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?",
                 list(updates.values()) + [uid])
    conn.commit()
    row = conn.execute("SELECT id,nome,login,perfil,ativo,criado_em FROM usuarios WHERE id=?", (uid,)).fetchone()
    conn.close()
    json_response(handler, dict(row))

def handle_delete_usuario(handler, uid):
    admin = require_admin(handler)
    if not admin: return
    if int(uid) == admin['id']:
        return error_response(handler, 'Você não pode remover sua própria conta')
    conn = get_db()
    conn.execute("UPDATE usuarios SET ativo=0 WHERE id=?", (uid,))
    conn.commit(); conn.close()
    json_response(handler, {'ok': True})

def handle_trocar_senha(handler):
    """Qualquer usuário pode trocar sua própria senha."""
    usuario = require_auth(handler)
    if not usuario: return
    data       = read_body(handler)
    senha_nova = (data.get('senha_nova') or '').strip()
    senha_atual= (data.get('senha_atual') or '').strip()
    if not senha_atual or not senha_nova:
        return error_response(handler, 'senha_atual e senha_nova são obrigatórias')
    conn = get_db()
    ok = conn.execute("SELECT id FROM usuarios WHERE id=? AND senha_hash=?",
                      (usuario['id'], hash_senha(senha_atual))).fetchone()
    if not ok: conn.close(); return error_response(handler, 'Senha atual incorreta', 401)
    conn.execute("UPDATE usuarios SET senha_hash=? WHERE id=?", (hash_senha(senha_nova), usuario['id']))
    conn.commit(); conn.close()
    json_response(handler, {'ok': True})

# ── ROTAS PROCESSOS ───────────────────────────────────────────────────────────

def handle_get_processos(handler, qs):
    if not require_auth(handler): return
    conn = get_db()
    page     = int(qs.get('page',     ['1'])[0])
    per_page = int(qs.get('per_page', ['50'])[0])
    search   = qs.get('q',            [''])[0].strip()
    status   = qs.get('status',       [''])[0].strip()
    servidor = qs.get('servidor',     [''])[0].strip()
    sensib   = qs.get('sensibilidade',[''])[0].strip()
    modal    = qs.get('modalidade',   [''])[0].strip()
    tema     = qs.get('tema',         [''])[0].strip()
    situacao = qs.get('situacao_final',[''])[0].strip()
    sort     = qs.get('sort',         ['data_entrada'])[0]
    order    = qs.get('order',        ['DESC'])[0].upper()
    if order not in ('ASC','DESC'): order = 'DESC'
    allowed  = {'data_entrada','prazo_final','numero_processo','tema','status','sensibilidade','servidor_atribuido'}
    if sort not in allowed: sort = 'data_entrada'
    where, params = ["1=1"], []
    if search:
        where.append("(numero_processo LIKE ? OR tema LIKE ? OR assunto LIKE ? OR sintese LIKE ? OR nome_envolvidos LIKE ? OR nome_local LIKE ?)")
        s = f"%{search}%"; params += [s,s,s,s,s,s]
    if status:   where.append("status=?");             params.append(status)
    if servidor: where.append("servidor_atribuido=?"); params.append(servidor)
    if sensib:   where.append("sensibilidade=?");      params.append(sensib)
    if modal:    where.append("modalidade=?");         params.append(modal)
    if tema:     where.append("tema=?");               params.append(tema)
    if situacao: where.append("situacao_final=?");     params.append(situacao)
    w      = " AND ".join(where)
    total  = conn.execute(f"SELECT COUNT(*) FROM processos WHERE {w}", params).fetchone()[0]
    offset = (page-1)*per_page
    rows   = conn.execute(f"SELECT * FROM processos WHERE {w} ORDER BY {sort} {order} LIMIT ? OFFSET ?", params+[per_page,offset]).fetchall()
    conn.close()
    json_response(handler, {'total':total,'page':page,'per_page':per_page,'pages':(total+per_page-1)//per_page,'data':rows_to_list(rows)})

def handle_get_processo(handler, pid):
    if not require_auth(handler): return
    conn = get_db()
    row = conn.execute("SELECT * FROM processos WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not row: return error_response(handler, 'Não encontrado', 404)
    json_response(handler, dict(row))

PROCESSO_FIELDS = ['frente','numero_processo','entrada','data_entrada','prazo_final',
                   'modalidade','tema','assunto','descritivo','retorno','envolvidos',
                   'local','sensibilidade','servidor_atribuido','status','inicio_tratamento',
                   'sintese','nome_envolvidos','nome_local','pendencias_area','sintese_parecer',
                   'data_finalizacao','unidade_envio','data_envio','prazo_devolucao',
                   'data_devolucao','situacao_final','observacoes','tempo_medio_atendimento']

def handle_create_processo(handler):
    usuario = require_auth(handler)
    if not usuario: return
    data = read_body(handler)
    if not data.get('numero_processo'): return error_response(handler, 'numero_processo obrigatório')
    conn = get_db()
    cols = [f for f in PROCESSO_FIELDS if f in data]
    vals = [data[f] for f in cols]
    cur  = conn.execute(f"INSERT INTO processos ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", vals)
    pid  = cur.lastrowid
    conn.execute("INSERT INTO log_alteracoes(processo_id,acao,campo,valor_antes,valor_depois,usuario_id,usuario_nome) VALUES(?,?,?,?,?,?,?)",
                 (pid, 'criação', 'numero_processo', '', data.get('numero_processo',''), usuario['id'], usuario['nome']))
    conn.commit()
    row = conn.execute("SELECT * FROM processos WHERE id=?", (pid,)).fetchone()
    conn.close()
    json_response(handler, dict(row), 201)

def handle_update_processo(handler, pid):
    usuario = require_auth(handler)
    if not usuario: return
    data = read_body(handler)
    conn = get_db()
    row  = conn.execute("SELECT * FROM processos WHERE id=?", (pid,)).fetchone()
    if not row: conn.close(); return error_response(handler, 'Não encontrado', 404)
    old     = dict(row)
    updates = {k:v for k,v in data.items() if k in PROCESSO_FIELDS}
    if not updates: conn.close(); return error_response(handler, 'Nenhum campo para atualizar')
    for k, v in updates.items():
        if str(old.get(k) or '') != str(v or ''):
            conn.execute(
                "INSERT INTO log_alteracoes(processo_id,acao,campo,valor_antes,valor_depois,usuario_id,usuario_nome) VALUES(?,?,?,?,?,?,?)",
                (pid, 'edição', k, old.get(k,''), v, usuario['id'], usuario['nome'])
            )
    conn.execute(f"UPDATE processos SET {', '.join(f'{k}=?' for k in updates)}, atualizado_em=datetime('now','localtime') WHERE id=?",
                 list(updates.values())+[pid])
    conn.commit()
    row = conn.execute("SELECT * FROM processos WHERE id=?", (pid,)).fetchone()
    conn.close()
    json_response(handler, dict(row))

def handle_delete_processo(handler, pid):
    usuario = require_auth(handler)
    if not usuario: return
    conn = get_db()
    row = conn.execute("SELECT numero_processo FROM processos WHERE id=?", (pid,)).fetchone()
    if not row: conn.close(); return error_response(handler, 'Não encontrado', 404)
    conn.execute("INSERT INTO log_alteracoes(processo_id,acao,campo,valor_antes,valor_depois,usuario_id,usuario_nome) VALUES(?,?,?,?,?,?,?)",
                 (pid, 'exclusão', 'numero_processo', row['numero_processo'], '', usuario['id'], usuario['nome']))
    conn.execute("DELETE FROM processos WHERE id=?", (pid,))
    conn.commit(); conn.close()
    json_response(handler, {'ok': True})

def handle_get_stats(handler):
    if not require_auth(handler): return
    conn = get_db()
    hoje = date.today().isoformat()
    s = {}
    s['total']             = conn.execute("SELECT COUNT(*) FROM processos").fetchone()[0]
    s['em_tramitacao']     = conn.execute("SELECT COUNT(*) FROM processos WHERE status='EM TRAMITAÇÃO'").fetchone()[0]
    s['prorrogado']        = conn.execute("SELECT COUNT(*) FROM processos WHERE status='PRORROGADO'").fetchone()[0]
    s['finalizado']        = conn.execute("SELECT COUNT(*) FROM processos WHERE situacao_final LIKE '%finalizado%'").fetchone()[0]
    s['alta_sensibilidade']= conn.execute("SELECT COUNT(*) FROM processos WHERE sensibilidade='ALTA'").fetchone()[0]
    s['em_atraso']         = conn.execute(
        "SELECT COUNT(*) FROM processos WHERE prazo_final<? AND situacao_final NOT LIKE '%finalizado%' AND prazo_final IS NOT NULL AND prazo_final!=''", (hoje,)
    ).fetchone()[0]
    for col, key in [('modalidade','por_modalidade'),('servidor_atribuido','por_servidor'),
                     ('sensibilidade','por_sensibilidade'),('tema','por_tema'),
                     ('status','por_status'),('situacao_final','por_situacao_final')]:
        lim = "LIMIT 10" if key == 'por_tema' else ""
        s[key] = rows_to_list(conn.execute(
            f"SELECT {col}, COUNT(*) as total FROM processos WHERE {col}!='' GROUP BY {col} ORDER BY total DESC {lim}"
        ).fetchall())
    s['por_mes'] = rows_to_list(conn.execute(
        "SELECT substr(data_entrada,1,7) as mes, COUNT(*) as total FROM processos WHERE data_entrada IS NOT NULL AND data_entrada!='' GROUP BY mes ORDER BY mes DESC LIMIT 12"
    ).fetchall())
    conn.close()
    json_response(handler, s)

def handle_get_categorias(handler):
    if not require_auth(handler): return
    conn = get_db()
    rows = rows_to_list(conn.execute("SELECT * FROM categorias WHERE ativo=1 ORDER BY tipo, pai_id NULLS FIRST, valor").fetchall())
    conn.close()
    json_response(handler, rows)

def handle_create_categoria(handler):
    if not require_auth(handler): return
    data  = read_body(handler)
    tipo  = (data.get('tipo')  or '').strip()
    valor = (data.get('valor') or '').strip()
    pai   = data.get('pai_id') or None
    if not tipo or not valor: return error_response(handler, 'tipo e valor são obrigatórios')
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO categorias(tipo,valor,pai_id) VALUES(?,?,?)", (tipo, valor, pai))
        conn.commit()
        row = conn.execute("SELECT * FROM categorias WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
        json_response(handler, dict(row), 201)
    except sqlite3.IntegrityError:
        conn.close()
        error_response(handler, f'"{valor}" já existe nesta categoria', 409)

def handle_delete_categoria(handler, cid):
    if not require_auth(handler): return
    conn = get_db()
    conn.execute("UPDATE categorias SET ativo=0 WHERE id=?", (cid,))
    conn.commit(); conn.close()
    json_response(handler, {'ok': True})

def handle_get_log(handler, pid):
    if not require_auth(handler): return
    conn = get_db()
    rows = rows_to_list(conn.execute(
        "SELECT * FROM log_alteracoes WHERE processo_id=? ORDER BY momento DESC", (pid,)
    ).fetchall())
    conn.close()
    json_response(handler, rows)

def handle_export_csv(handler, qs):
    if not require_auth(handler): return
    conn = get_db()
    search = qs.get('q',     [''])[0].strip()
    status = qs.get('status',[''])[0].strip()
    where, params = ["1=1"], []
    if search:
        where.append("(numero_processo LIKE ? OR tema LIKE ?)"); params += [f"%{search}%",f"%{search}%"]
    if status:
        where.append("status=?"); params.append(status)
    rows = conn.execute(f"SELECT * FROM processos WHERE {' AND '.join(where)} ORDER BY data_entrada DESC", params).fetchall()
    conn.close()
    out = io.StringIO()
    if rows:
        writer = csv_mod.DictWriter(out, fieldnames=dict(rows[0]).keys())
        writer.writeheader()
        for r in rows: writer.writerow(dict(r))
    body = out.getvalue().encode('utf-8-sig')
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/csv; charset=utf-8-sig')
    handler.send_header('Content-Disposition', 'attachment; filename="processos_ouvidoria.csv"')
    handler.send_header('Content-Length', len(body))
    handler.end_headers()
    handler.wfile.write(body)

def handle_import_csv(handler):
    usuario = require_auth(handler)
    if not usuario: return
    data    = read_body(handler)
    rows    = data.get('rows', [])
    mode    = data.get('mode', 'skip')
    if not rows: return error_response(handler, 'Nenhuma linha recebida')
    conn = get_db()
    inserted, updated, skipped, errors = 0, 0, 0, []
    for i, r in enumerate(rows):
        num = (r.get('numero_processo') or r.get('NÚMERO DO PROCESSO/PROTOCOLO') or '').strip()
        if not num: errors.append(f"Linha {i+2}: numero_processo vazio"); continue
        norm     = _normalize_row(r)
        existing = conn.execute("SELECT id FROM processos WHERE numero_processo=?", (num,)).fetchone()
        if existing:
            if mode == 'update':
                cols = [f for f in PROCESSO_FIELDS if f in norm and f != 'numero_processo']
                if cols:
                    conn.execute(f"UPDATE processos SET {', '.join(f'{c}=?' for c in cols)}, atualizado_em=datetime('now','localtime') WHERE numero_processo=?",
                                 [norm[c] for c in cols]+[num])
                    conn.execute("INSERT INTO log_alteracoes(processo_id,acao,usuario_id,usuario_nome) VALUES(?,?,?,?)",
                                 (existing['id'], 'importação (atualização)', usuario['id'], usuario['nome']))
                    updated += 1
                else: skipped += 1
            else: skipped += 1
        else:
            cols = [f for f in PROCESSO_FIELDS if f in norm]
            if 'numero_processo' not in cols: cols.insert(0,'numero_processo'); norm['numero_processo']=num
            try:
                cur = conn.execute(f"INSERT INTO processos ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", [norm[c] for c in cols])
                conn.execute("INSERT INTO log_alteracoes(processo_id,acao,usuario_id,usuario_nome) VALUES(?,?,?,?)",
                             (cur.lastrowid, 'importação (novo)', usuario['id'], usuario['nome']))
                inserted += 1
            except Exception as e: errors.append(f"Linha {i+2} ({num}): {e}")
    conn.commit(); conn.close()
    json_response(handler, {'ok':True,'inserted':inserted,'updated':updated,'skipped':skipped,'errors':errors[:20]})

_CSV_MAP = {
    'frente':'frente','número do processo/protocolo':'numero_processo','numero_processo':'numero_processo',
    'entrada':'entrada','data de entrada':'data_entrada','data_entrada':'data_entrada',
    'prazo final de resolução':'prazo_final','prazo_final':'prazo_final','modalidade':'modalidade',
    'tema':'tema','assunto':'assunto','descritivo':'descritivo','retorno':'retorno',
    'envolvidos':'envolvidos','local':'local','sensibilidade':'sensibilidade',
    'servidor - atribuído':'servidor_atribuido','servidor_atribuido':'servidor_atribuido',
    'status':'status','tempo médio de atendimento':'tempo_medio_atendimento',
    'tempo_medio_atendimento':'tempo_medio_atendimento','inicio do tratamento':'inicio_tratamento',
    'inicio_tratamento':'inicio_tratamento','síntese':'sintese','sintese':'sintese',
    'nome-envolvidos':'nome_envolvidos','nome_envolvidos':'nome_envolvidos',
    'nome-local':'nome_local','nome_local':'nome_local','retorno2':'retorno',
    'pendências da área':'pendencias_area','pendencias_area':'pendencias_area',
    'síntese do parecer da área':'sintese_parecer','sintese_parecer':'sintese_parecer',
    'data de finalização em sistema':'data_finalizacao','data_finalizacao':'data_finalizacao',
    'unidade de envio':'unidade_envio','unidade_envio':'unidade_envio',
    'data de envio':'data_envio','data_envio':'data_envio',
    'prazo de devolução':'prazo_devolucao','prazo_devolucao':'prazo_devolucao',
    'data de devolução':'data_devolucao','data_devolucao':'data_devolucao',
    'situação final':'situacao_final','situacao_final':'situacao_final',
    'observacoes':'observacoes','observações':'observacoes',
}
def _normalize_row(r):
    out = {}
    for k, v in r.items():
        mapped = _CSV_MAP.get(k.strip().lower())
        if mapped:
            val = str(v).strip() if v is not None else ''
            if mapped in ('data_entrada','prazo_final','inicio_tratamento','data_finalizacao','data_envio','prazo_devolucao','data_devolucao'):
                val = _parse_date(val)
            out[mapped] = val if val != '' else None
    return out
def _parse_date(s):
    if not s: return None
    s = s.strip()
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', s)
    if m: return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    m = re.match(r'^\d{4}-\d{2}-\d{2}$', s)
    if m: return s
    return None

# ── HTTP HANDLER ──────────────────────────────────────────────────────────────

STATIC_DIR = os.path.join(os.path.dirname(__file__), "public")

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path); path = parsed.path.rstrip('/'); qs = parse_qs(parsed.query)
        try:
            if path == '/api/me':           return handle_me(self)
            if path == '/api/processos':    return handle_get_processos(self, qs)
            if path == '/api/stats':        return handle_get_stats(self)
            if path == '/api/categorias':   return handle_get_categorias(self)
            if path == '/api/export':       return handle_export_csv(self, qs)
            if path == '/api/usuarios':     return handle_get_usuarios(self)
            m = re.match(r'^/api/processos/(\d+)/log$', path)
            if m: return handle_get_log(self, int(m.group(1)))
            m = re.match(r'^/api/processos/(\d+)$', path)
            if m: return handle_get_processo(self, int(m.group(1)))
            # static
            fpath = os.path.join(STATIC_DIR, (path or '/index.html').lstrip('/'))
            if not os.path.isfile(fpath): fpath = os.path.join(STATIC_DIR, 'index.html')
            ct = {'.html':'text/html','.js':'application/javascript','.css':'text/css','.ico':'image/x-icon'}.get(os.path.splitext(fpath)[1],'application/octet-stream')
            body = open(fpath,'rb').read()
            self.send_response(200); self.send_header('Content-Type',ct); self.send_header('Content-Length',len(body)); self.end_headers(); self.wfile.write(body)
        except Exception: traceback.print_exc(); error_response(self, 'Erro interno', 500)

    def do_POST(self):
        parsed = urlparse(self.path); path = parsed.path.rstrip('/')
        try:
            if path == '/api/login':       return handle_login(self)
            if path == '/api/logout':      return handle_logout(self)
            if path == '/api/trocar-senha':return handle_trocar_senha(self)
            if path == '/api/processos':   return handle_create_processo(self)
            if path == '/api/categorias':  return handle_create_categoria(self)
            if path == '/api/import':      return handle_import_csv(self)
            if path == '/api/usuarios':    return handle_create_usuario(self)
            error_response(self, 'Rota não encontrada', 404)
        except Exception: traceback.print_exc(); error_response(self, 'Erro interno', 500)

    def do_PUT(self):
        parsed = urlparse(self.path); path = parsed.path.rstrip('/')
        try:
            m = re.match(r'^/api/processos/(\d+)$', path)
            if m: return handle_update_processo(self, int(m.group(1)))
            m = re.match(r'^/api/usuarios/(\d+)$', path)
            if m: return handle_update_usuario(self, int(m.group(1)))
            error_response(self, 'Rota não encontrada', 404)
        except Exception: traceback.print_exc(); error_response(self, 'Erro interno', 500)

    def do_DELETE(self):
        parsed = urlparse(self.path); path = parsed.path.rstrip('/')
        try:
            m = re.match(r'^/api/processos/(\d+)$', path)
            if m: return handle_delete_processo(self, int(m.group(1)))
            m = re.match(r'^/api/categorias/(\d+)$', path)
            if m: return handle_delete_categoria(self, int(m.group(1)))
            m = re.match(r'^/api/usuarios/(\d+)$', path)
            if m: return handle_delete_usuario(self, int(m.group(1)))
            error_response(self, 'Rota não encontrada', 404)
        except Exception: traceback.print_exc(); error_response(self, 'Erro interno', 500)

# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("⚙️  Iniciando banco de dados...")
    init_db()
    print(f"✅ Banco pronto: {DB_PATH}")
    os.makedirs(STATIC_DIR, exist_ok=True)
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f"🚀 Servidor rodando em http://localhost:{PORT}")
    print("   Pressione Ctrl+C para encerrar.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Servidor encerrado.")
