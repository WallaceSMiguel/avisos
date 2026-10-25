# -*- coding: utf-8 -*-
"""
LEITOR DO MESSENGER (Gestta / Onvio)
====================================

Abre o Messenger em segundo plano, lê a aba "Pendentes" a cada
poucos segundos e grava o resultado em  dados/atendimento.json.
O painel (app.py) lê esse arquivo e mostra no card da TV.

NÃO GUARDA SENHA. O login é feito UMA vez, à mão:

    login_messenger.bat        (ou: python leitor_messenger.py --login)

Abre o Google Chrome NORMAL (sem automação) na página de login do Onvio.
Você entra com usuário e senha, abre o Messenger pelo próprio Onvio e,
quando aparecer a aba "Pendentes", FECHA o Chrome. A sessão fica salva
na pasta "perfil_messenger" e o leitor passa a usar essa sessão.
Se um dia a sessão expirar, o card na TV avisa "Login necessário":
é só rodar o login_messenger.bat de novo.

Uso normal (o iniciar_painel.bat já faz isso):
    python leitor_messenger.py
"""

import configparser
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.ini"
PERFIL_DIR = BASE_DIR / "perfil_messenger"
SAIDA_JSON = BASE_DIR / "dados" / "atendimento.json"
DEBUG_HTML = BASE_DIR / "dados" / "messenger_debug.html"
DEBUG_PNG = BASE_DIR / "dados" / "leitor_debug.png"
LOG_FILE = BASE_DIR / "dados" / "leitor.log"


class Espelho:
    """Mostra na janela E grava em dados/leitor.log (para o /diagnostico)."""

    def __init__(self, original):
        self.original = original

    def write(self, texto):
        try:
            self.original.write(texto)
        except Exception:
            pass
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_000_000:
                LOG_FILE.write_text("", encoding="utf-8")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(texto)
        except Exception:
            pass

    def flush(self):
        try:
            self.original.flush()
        except Exception:
            pass

URL_PADRAO = "https://app.gestta.com.br/attendance/#/chat/pending"
URL_LOGIN_PADRAO = "https://onvio.com.br/login/#/logout"

# Nomes de departamento procurados em cada conversa pendente
# (pode ser trocado no config.ini -> departamentos = ...)
DEPARTAMENTOS_PADRAO = (
    "Fiscal, Contábil, Departamento Pessoal, Pessoal, DP, Legalização, "
    "Societário, Paralegal, Financeiro, Comercial, Atendimento, RH, TI"
)

# Recarrega a página de tempos em tempos (evita travar/consumir memória)
RECARREGAR_A_CADA = 30 * 60


# ============================================================
# CONFIGURAÇÃO
# ============================================================

def ler_config():
    cfg = configparser.ConfigParser()
    try:
        cfg.read(CONFIG_FILE, encoding="utf-8-sig")
    except Exception as erro:
        print("⚠ Erro ao ler config.ini:", erro)

    def pegar(chave, padrao):
        return cfg.get("messenger", chave, fallback=padrao).strip() or padrao

    def inteiro(chave, padrao):
        try:
            return max(3, int(pegar(chave, str(padrao))))
        except ValueError:
            return padrao

    return {
        "ativo": pegar("ativo", "sim").lower() in {"sim", "s", "1", "true"},
        "url": pegar("url", URL_PADRAO),
        "url_login": pegar("url_login", URL_LOGIN_PADRAO),
        "intervalo": inteiro("intervalo", 10),
        "exibir_nomes": pegar("exibir_nomes", "sim").lower(),
        "departamentos": [
            d.strip() for d in pegar("departamentos", DEPARTAMENTOS_PADRAO).split(",")
            if d.strip()
        ],
        "navegador": pegar("navegador", "chrome").lower(),
        "visivel": pegar("janela_visivel", "nao").lower() in {"sim", "s", "1", "true"},
        "caminho_chrome": pegar("caminho_chrome", ""),
    }


# ============================================================
# GRAVAR RESULTADO (escrita atômica: o app.py nunca lê pela metade)
# ============================================================

def gravar(dados):
    dados["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
    dados["atualizado_ts"] = time.time()
    SAIDA_JSON.parent.mkdir(parents=True, exist_ok=True)
    temp = SAIDA_JSON.with_suffix(".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(temp, SAIDA_JSON)


def _sem_acento(t):
    import unicodedata
    t = unicodedata.normalize("NFD", t or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower().strip()


def ler_frase_solicitacao(textos):
    """
    O Messenger mostra na conversa uma frase do sistema como:
      "Fulano solicitou atendimento do departamento Departamento Contábil, aguarde..."
    Daqui tiramos o NOME e o DEPARTAMENTO exatos.
    Se a frase vier cortada na tela ("...Departamento Cont..."), o
    departamento volta com "..." para ser completado pela lista do config.ini.
    """
    import re
    for t in textos or []:
        t = (t or "").strip()
        base, cortado = limpar_reticencias(t)
        m = re.search(
            r"^(.*?)\s+solicitou atendimento (?:do|no|ao|para o)?\s*departamento\s+(.+?)\s*(?:[,;!]|\.\s|\.$|$|\s+aguard)",
            base, re.IGNORECASE,
        )
        if m:
            nome = m.group(1).strip(" -:")
            depto = m.group(2).strip(" -:")
            if cortado and m.end(2) >= len(base.rstrip(" .")):
                depto += "..."
            return nome, depto
    return "", ""


def ler_tempo_na_pagina(textos):
    """
    Converte o tempo que o Messenger mostra na conversa em minutos:
    "poucos segundos" -> 0 ; "2 minutos" / "há 5 min" -> 2 / 5 ;
    "uma hora" / "3 horas" -> 60 / 180 ; "um dia" -> 1440.
    Retorna None se não achar.
    """
    import re
    numeros = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "três": 3}
    for t in textos or []:
        tn = _sem_acento(t)
        if len(tn) > 40:
            continue
        if "poucos segundos" in tn or tn in {"agora", "agora mesmo"}:
            return 0
        m = re.search(r"(\d+|um|uma|dois|duas|tres)\s*(min|minuto|minutos|h|hora|horas|dia|dias)\b", tn)
        if m:
            n = int(m.group(1)) if m.group(1).isdigit() else numeros.get(m.group(1), 1)
            unidade = m.group(2)
            if unidade.startswith("min"):
                return n
            if unidade.startswith("h"):
                return n * 60
            return n * 1440
        if re.search(r"\bum minuto\b", tn):
            return 1
    return None


def limpar_reticencias(t):
    t = (t or "").strip()
    for fim in ("...", "…"):
        if t.endswith(fim):
            return t[: -len(fim)].strip(), True
    return t, False


def resolver_departamento(lido, textos, nome, departamentos):
    import re
    """
    1) etiqueta lida direto da conversa (ex.: "Departamento Pess...")
       -> completa pelo nome da lista do config.ini
    2) se não houver, procura um departamento entre os textos.
    """
    if lido:
        base, cortado = limpar_reticencias(lido)
        bn = _sem_acento(base)
        if not cortado:
            return base                      # nome completo: mostra como veio
        sem_prefixo = re.sub(r"^(departamento|depto\.?)\s+", "", bn)
        for d in departamentos:
            dn = _sem_acento(d)
            if len(bn) >= 3 and dn.startswith(bn):
                return d
        for d in departamentos:
            dn = _sem_acento(d)
            if len(sem_prefixo) >= 3 and dn.startswith(sem_prefixo):
                return ("Departamento " + d) if sem_prefixo != bn else d
        if base:
            return base + ("…" if cortado else "")
    return achar_departamento(textos, nome, departamentos)


def achar_departamento(textos, nome, departamentos):
    """
    Procura um nome de departamento entre os textos da conversa.
    1º texto IGUAL a um departamento; 2º texto que CONTÉM o departamento
    como palavra (ex.: "Depto. Fiscal"). O nome do cliente é ignorado.
    """
    import re
    nome_n = _sem_acento(nome)
    candidatos = [t for t in (textos or []) if _sem_acento(t) != nome_n]
    deps = [(d, _sem_acento(d)) for d in departamentos]

    for t in candidatos:
        tn = _sem_acento(t)
        for original, dn in deps:
            if tn == dn:
                return original
    # texto cortado na tela: "Departamento Pess..." -> "Departamento Pessoal"
    for t in candidatos:
        base, cortado = limpar_reticencias(t)
        if not cortado:
            continue
        bn = _sem_acento(base)
        for original, dn in deps:
            if len(bn) >= 3 and dn.startswith(bn):
                return original
    for t in candidatos:
        tn = _sem_acento(t)
        if len(tn) > 40:          # prévia de mensagem, não é departamento
            continue
        for original, dn in deps:
            if re.search(r"(^|[^a-z0-9])" + re.escape(dn) + r"($|[^a-z0-9])", tn):
                return original
    return ""


def formatar_nome(nome, modo):
    nome = (nome or "").strip()
    if not nome:
        return ""
    if modo in {"nao", "não", "n", "0", "false"}:
        return ""
    if modo in {"primeiro_nome", "primeiro"}:
        # "REVANCHE - JANE" -> "JANE" ; "Maria Silva" -> "Maria"
        parte = nome.split(" - ")[-1].strip() or nome
        return parte.split()[0].title()
    return nome


# ============================================================
# LEITURA DA PÁGINA
# Usa apenas atributos estáveis da página do Messenger:
#   data-qe-id="attendance-tab-pending"      (aba Pendentes)
#   data-qe-id="attendance-tab-ongoing"      (aba Em atendimento)
#   name="conversation-list-container"       (lista de conversas)
#   title="..."                              (nome / telefone)
# As classes (sc-xxxx) mudam a cada versão do site e não são usadas.
# ============================================================

JS_LER_PENDENTES = r"""
() => {
    const abaPend = document.querySelector('[data-qe-id="attendance-tab-pending"]');
    const abaAtend = document.querySelector('[data-qe-id="attendance-tab-ongoing"]');
    if (!abaPend) {
        return { ok: false, motivo: "sem_aba" };
    }

    // garante que a aba Pendentes está selecionada
    if (!/active-true/.test(abaPend.className)) {
        abaPend.click();
        return { ok: false, motivo: "trocando_aba" };
    }

    const numeroDaAba = (aba, rotulo) => {
        if (!aba) return null;
        const t = (aba.innerText || "").replace(rotulo, "").replace(/\s+/g, " ").trim();
        const m = t.match(/\d+/);
        return m ? parseInt(m[0], 10) : 0;
    };

    // lista de conversas que pertence à mesma área das abas
    // (escolhe a lista MAIS PRÓXIMA da aba - a outra é a de Contatos)
    let lista = null;
    let menorDistancia = 999;
    const listas = document.querySelectorAll('[name="conversation-list-container"]');
    for (const c of listas) {
        let a = c.parentElement;
        for (let i = 0; i < 12 && a; i++, a = a.parentElement) {
            if (a.contains(abaPend)) {
                if (i < menorDistancia) { menorDistancia = i; lista = c; }
                break;
            }
        }
    }

    const itens = [];
    const ehTelefone = t => /^\+?[\d\s()\-]{8,}$/.test(t);
    const ehFrase = t => t.length > 60 || /solicitou|atendimento|aguarde/i.test(t);
    const semTelefone = t => t.replace(/\s*\(\+?\d[\d\s()\-]{7,}\)\s*$/, "").trim();

    if (lista) {
        // "Trilho" da lista virtual: o filho com atributo height="...px".
        // Cada filho do trilho é UMA conversa.
        let trilho = null;
        for (const f of Array.from(lista.children)) {
            if (f.hasAttribute("height")) { trilho = f; break; }
        }
        if (!trilho) trilho = lista.firstElementChild || lista;

        const blocos = Array.from(trilho.children).filter(b =>
            (b.innerText || "").trim() || b.querySelector("[title]"));

        for (const bloco of blocos) {
            const texto = (bloco.innerText || "").trim();
            const linhas = texto.split("\n").map(x => x.trim()).filter(Boolean);

            // títulos (title="...") do bloco, na ordem em que aparecem
            const titulos = Array.from(bloco.querySelectorAll("[title]"))
                .map(el => (el.getAttribute("title") || "").trim())
                .filter(t => t && t !== "--");

            // NOME: 1º título que não é telefone nem frase; depois 1ª linha de texto
            let nome = "";
            for (const t of titulos) {
                if (!ehTelefone(t) && !ehFrase(t)) { nome = semTelefone(t); break; }
            }
            if (!nome) {
                nome = semTelefone(linhas.find(l => !ehTelefone(l) && !ehFrase(l)) || "");
            }

            const hora = (texto.match(/\b\d{1,2}:\d{2}\b/) || [""])[0];

            // todos os textos (para achar departamento e tempo)
            const vistos = {};
            const textos = [];
            titulos.concat(linhas).forEach(t => {
                if (!t || vistos[t]) return;
                if (ehTelefone(t)) return;
                vistos[t] = true;
                textos.push(t.replace(/\(\+?\d[\d\s()\-]{7,}\)/g, "").trim().slice(0, 220));
            });

            // etiqueta de departamento (chip "Departamento ...")
            let departamento = "";
            Array.from(bloco.querySelectorAll("*")).forEach(el => {
                const tit = (el.getAttribute("title") || "").trim();
                const txt = (el.textContent || "").trim();
                [tit, txt].forEach(t => {
                    if (!t || t.length > 60 || /solicitou/i.test(t)) return;
                    if (!/departamento|depto/i.test(t)) return;
                    const cortado = /(\.\.\.|…)$/.test(t);
                    const atualCortado = /(\.\.\.|…)$/.test(departamento);
                    if (!departamento || (atualCortado && !cortado) ||
                        (cortado === atualCortado && t.length < departamento.length)) {
                        departamento = t;
                    }
                });
            });

            itens.push({ nome: nome, hora: hora, departamento: departamento,
                         textos: textos.slice(0, 15) });
        }
    }

    const badge = numeroDaAba(abaPend, "Pendentes");
    return {
        ok: true,
        pendentes_badge: badge,
        em_atendimento: numeroDaAba(abaAtend, "Em atendimento"),
        itens: itens,
        achou_lista: !!lista
    };
}
"""


def parece_tela_de_login(page):
    url = (page.url or "").lower()
    if "/attendance" not in url:
        return True
    try:
        senha = page.query_selector('input[type="password"]')
        return senha is not None
    except Exception:
        return False


# ============================================================
# NAVEGADOR
# ============================================================

def abrir_contexto(playwright, cfg, visivel, oculto_fora_da_tela=False):
    """
    visivel=True             -> janela normal na tela
    oculto_fora_da_tela=True -> janela "de verdade", mas posicionada fora
                                da tela. Sites como o Onvio/Gestta costumam
                                BLOQUEAR o modo headless (sem janela), por
                                isso o leitor usa este modo.
    """
    PERFIL_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        "--disable-blink-features=AutomationControlled",
        # não deixa o Chrome "adormecer" a página em segundo plano
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    headless = not visivel
    if oculto_fora_da_tela:
        headless = False
        args += ["--window-position=-32000,-32000", "--window-size=1366,900"]
    opcoes = dict(
        user_data_dir=str(PERFIL_DIR),
        headless=headless,
        viewport={"width": 1366, "height": 900},
        locale="pt-BR",
        args=args,
        # tira a marca "Chrome está sendo controlado por software de teste",
        # que faz alguns sites (como o login do Onvio) bloquearem a navegação
        ignore_default_args=["--enable-automation"],
    )
    canal = cfg["navegador"]
    if canal in {"msedge", "chrome"}:
        try:
            return playwright.chromium.launch_persistent_context(channel=canal, **opcoes)
        except Exception as erro:
            texto_erro = str(erro).lower()
            # só usa o Chromium reserva se o Chrome NÃO estiver instalado;
            # se o erro for perfil em uso, tentar outro navegador só piora
            if "executable doesn't exist" not in texto_erro and "not found" not in texto_erro:
                raise
            print(f"⚠ {canal} não encontrado. Usando o Chromium do Playwright...")
    return playwright.chromium.launch_persistent_context(**opcoes)


def achar_chrome(cfg):
    """Procura o chrome.exe nos lugares padrão do Windows."""
    candidatos = []
    if cfg.get("caminho_chrome"):
        candidatos.append(cfg["caminho_chrome"])
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidatos.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    candidatos += ["/usr/bin/google-chrome", "/usr/bin/chromium"]
    for c in candidatos:
        if c and os.path.isfile(c):
            return c
    return None


def conferir_sessao(cfg):
    """Abre o Messenger em segundo plano com a sessão salva e confere."""
    from playwright.sync_api import sync_playwright

    print("\nConferindo a sessão (abrindo o Messenger em segundo plano)...")
    with sync_playwright() as p:
        ctx = abrir_contexto(p, cfg, visivel=False, oculto_fora_da_tela=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector('[data-qe-id="attendance-tab-pending"]', timeout=40000)
            print("✔ Login OK! A sessão foi salva. Pode iniciar o painel.")
            ok = True
        except Exception:
            ok = False
            try:
                foto = BASE_DIR / "dados" / "login_debug.png"
                foto.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(foto), full_page=True)
                print("⚠ Não consegui abrir o Messenger com a sessão salva.")
                print("  Endereço onde parou:", page.url)
                print("  Foto da tela salva em:", foto)
            except Exception:
                print("⚠ Não consegui abrir o Messenger com a sessão salva.")
        ctx.close()
    return ok


def resetar_posicao_janela():
    """
    O leitor usa uma janela FORA da tela. O Chrome grava essa posição no
    perfil e, no próximo login, abriria a janela lá fora de novo.
    Aqui apagamos a posição salva para a janela do login abrir normal.
    """
    for pref in (PERFIL_DIR / "Default" / "Preferences", PERFIL_DIR / "Local State"):
        if not pref.exists():
            continue
        try:
            dados = json.loads(pref.read_text(encoding="utf-8"))
        except Exception:
            continue
        mudou = False
        navegador = dados.get("browser")
        if isinstance(navegador, dict):
            for chave in ("window_placement", "app_window_placement"):
                if chave in navegador:
                    navegador.pop(chave, None)
                    mudou = True
        if mudou:
            try:
                pref.write_text(json.dumps(dados), encoding="utf-8")
            except Exception:
                pass


def perfil_em_uso():
    """No Windows o Chrome trava o arquivo 'lockfile' enquanto usa o perfil."""
    trava = PERFIL_DIR / "lockfile"
    if not trava.exists():
        return False
    try:
        os.remove(trava)
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def trazer_janela_para_tela(pid, segundos=40):
    """
    Windows: procura as janelas do Chrome do login e, se estiverem fora
    da tela (ou em qualquer lugar estranho), traz para a tela e maximiza.
    Roda em segundo plano durante os primeiros segundos do login.
    """
    if os.name != "nt":
        return
    import ctypes
    import threading
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    def pids_da_arvore(pid_raiz):
        # processo do Chrome + filhos
        pids = {pid_raiz}
        try:
            TH32CS_SNAPPROCESS = 0x2

            class PE(ctypes.Structure):
                _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                            ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]
            snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            pares = []
            e = PE()
            e.dwSize = ctypes.sizeof(PE)
            ok = kernel32.Process32First(snap, ctypes.byref(e))
            while ok:
                pares.append((e.th32ProcessID, e.th32ParentProcessID))
                ok = kernel32.Process32Next(snap, ctypes.byref(e))
            kernel32.CloseHandle(snap)
            mudou = True
            while mudou:
                mudou = False
                for filho, pai in pares:
                    if pai in pids and filho not in pids:
                        pids.add(filho)
                        mudou = True
        except Exception:
            pass
        return pids

    largura_tela = user32.GetSystemMetrics(0)
    altura_tela = user32.GetSystemMetrics(1)
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def corrigir():
        ja_corrigidas = set()
        fim = time.time() + segundos
        while time.time() < fim:
            pids = pids_da_arvore(pid)

            def callback(hwnd, _):
                if not user32.IsWindowVisible(hwnd):
                    return True
                pid_janela = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_janela))
                if pid_janela.value not in pids:
                    return True
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                fora = (r.right < 100 or r.left > largura_tela - 100 or
                        r.bottom < 50 or r.top > altura_tela - 50)
                if fora or hwnd not in ja_corrigidas:
                    user32.ShowWindow(hwnd, 9)                 # SW_RESTORE
                    user32.MoveWindow(hwnd, 40, 40, 1280, 860, True)
                    user32.ShowWindow(hwnd, 3)                 # SW_MAXIMIZE
                    user32.SetForegroundWindow(hwnd)
                    ja_corrigidas.add(hwnd)
                return True

            try:
                user32.EnumWindows(EnumWindowsProc(callback), 0)
            except Exception:
                return
            time.sleep(0.5)

    threading.Thread(target=corrigir, daemon=True).start()


def modo_login(cfg):
    PERFIL_DIR.mkdir(parents=True, exist_ok=True)

    if perfil_em_uso():
        print("=" * 64)
        print(" ⚠ O perfil do Messenger está em uso.")
        print("   Feche a janela 'Leitor Messenger' e o painel")
        print("   (iniciar_painel.bat) e rode o login de novo.")
        print("=" * 64)
        return False

    resetar_posicao_janela()
    chrome = achar_chrome(cfg)

    print("=" * 64)
    print(" LOGIN NO MESSENGER")
    print(" 1. Vai abrir o Google Chrome na página de login do Onvio.")
    print(" 2. Entre com o usuário e a senha (e o código, se pedir).")
    print(" 3. Dentro do Onvio, abra o MESSENGER normalmente, pelo menu.")
    print(" 4. Quando aparecer a tela com a aba 'Pendentes',")
    print("    FECHE o Chrome (no X da janela).")
    print(" 5. Este programa confere a sessão sozinho em seguida.")
    print("=" * 64)

    if not chrome:
        print("\n⚠ Não encontrei o Google Chrome instalado.")
        print("  Informe o caminho do chrome.exe no config.ini, seção [messenger]:")
        print("  caminho_chrome = C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")
        return False

    # Chrome NORMAL (sem Playwright): o site não vê automação nenhuma
    # e você navega livremente até o Messenger. Só usa uma pasta de
    # perfil separada, que depois o leitor reaproveita.
    processo = subprocess.Popen([
        chrome,
        "--user-data-dir=" + str(PERFIL_DIR),
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        # força a janela a abrir NA TELA e maximizada
        "--window-position=0,0",
        "--window-size=1280,900",
        "--start-maximized",
        cfg["url_login"],
    ])
    trazer_janela_para_tela(processo.pid)
    print("\nChrome aberto. Faça o login e abra o Messenger...")
    print("(esta janela espera você FECHAR o Chrome)")
    processo.wait()

    # dá um tempo para o Chrome terminar de gravar os cookies
    time.sleep(3)
    if not conferir_sessao(cfg):
        print("\n  Rode o login_messenger.bat de novo e, antes de fechar o Chrome,")
        print("  confirme que a aba 'Pendentes' do Messenger apareceu na tela.")
        return False
    return True


_TRAVA_SOCKET = None


def garantir_instancia_unica():
    """Impede dois leitores ao mesmo tempo (eles brigariam pelo perfil)."""
    import socket
    global _TRAVA_SOCKET
    try:
        _TRAVA_SOCKET = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _TRAVA_SOCKET.bind(("127.0.0.1", 50999))
        _TRAVA_SOCKET.listen(1)
        return True
    except OSError:
        return False


def fechar_chrome_orfao():
    """
    Fecha Chrome que ficou aberto usando a pasta perfil_messenger
    (sobra de um leitor fechado à força). Sem isso o próximo leitor
    falha com "Target page, context or browser has been closed".
    Só mexe nos Chrome deste perfil - o seu Chrome normal não é tocado.
    """
    if os.name != "nt":
        return
    comando = (
        "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -like '*perfil_messenger*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", comando],
            timeout=30, capture_output=True,
        )
    except Exception:
        pass
    try:
        (PERFIL_DIR / "lockfile").unlink()
    except Exception:
        pass
    time.sleep(2)


def modo_leitor(cfg):
    from playwright.sync_api import sync_playwright

    if not garantir_instancia_unica():
        print("Já existe um Leitor Messenger rodando neste PC. Esta janela pode ser fechada.")
        time.sleep(5)
        return

    fechar_chrome_orfao()
    erros_seguidos = 0

    primeira_vez = {}           # nome -> timestamp em que apareceu (tempo de espera)
    salvou_debug = False

    print("▶ Leitor do Messenger iniciado. Gravando em:", SAIDA_JSON)

    while True:
        try:
            with sync_playwright() as p:
                ctx = abrir_contexto(
                    p, cfg,
                    visivel=cfg["visivel"],
                    oculto_fora_da_tela=not cfg["visivel"],
                )
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                print("Abrindo", cfg["url"])
                gravar({"status": "carregando", "mensagem": "Abrindo o Messenger..."})
                page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
                carregou_em = time.time()
                falhas_seguidas = 0

                while True:
                    if time.time() - carregou_em > RECARREGAR_A_CADA:
                        page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
                        carregou_em = time.time()

                    try:
                        page.wait_for_selector(
                            '[data-qe-id="attendance-tab-pending"]', timeout=15000
                        )
                    except Exception:
                        falhas_seguidas += 1
                        endereco = page.url
                        try:
                            page.screenshot(path=str(DEBUG_PNG))
                        except Exception:
                            pass
                        if parece_tela_de_login(page):
                            print("⚠ Caiu na tela de login:", endereco)
                            print("  -> feche esta janela e rode o login_messenger.bat")
                            gravar({
                                "status": "login_necessario",
                                "mensagem": "Rode o login_messenger.bat no PC do painel",
                                "url": endereco,
                            })
                            time.sleep(60)
                            page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
                            continue
                        print(f"⚠ A aba 'Pendentes' não apareceu. Página atual: {endereco}")
                        print(f"  (foto da tela em {DEBUG_PNG})")
                        gravar({
                            "status": "carregando",
                            "mensagem": "A aba Pendentes não apareceu - veja /diagnostico",
                            "url": endereco,
                        })
                        if falhas_seguidas % 4 == 0:
                            page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
                        time.sleep(cfg["intervalo"])
                        continue

                    falhas_seguidas = 0
                    erros_seguidos = 0

                    resultado = page.evaluate(JS_LER_PENDENTES)
                    if not resultado.get("ok"):
                        time.sleep(2)
                        continue

                    itens = resultado.get("itens") or []
                    total = max(len(itens), resultado.get("pendentes_badge") or 0)
                    agora = time.time()

                    # tempo de espera: conta a partir de quando o leitor viu o cliente
                    chaves_atuais = set()
                    clientes = []
                    for i, item in enumerate(itens):
                        nome_frase, depto_frase = ler_frase_solicitacao(
                            (item.get("textos") or []) + [item.get("nome") or ""]
                        )
                        nome_item = (item.get("nome") or "").strip()
                        if nome_frase and (not nome_item or "solicitou" in nome_item.lower()):
                            nome_item = nome_frase
                        item["nome"] = nome_item
                        if depto_frase:
                            item["departamento"] = depto_frase
                        chave = (item.get("nome") or f"#{i}").strip()
                        chaves_atuais.add(chave)
                        desde = primeira_vez.setdefault(chave, agora)
                        # se a página informa há quanto tempo, usa o maior dos dois
                        min_pagina = ler_tempo_na_pagina(item.get("textos"))
                        if min_pagina is not None:
                            desde = min(desde, agora - min_pagina * 60)
                            primeira_vez[chave] = desde
                        clientes.append({
                            "nome": formatar_nome(item.get("nome"), cfg["exibir_nomes"]),
                            "departamento": resolver_departamento(
                                item.get("departamento"), item.get("textos"),
                                item.get("nome"), cfg["departamentos"]
                            ),
                            "hora": item.get("hora") or "",
                            "espera_min": int((agora - desde) // 60),
                        })
                    for chave in list(primeira_vez):
                        if chave not in chaves_atuais:
                            del primeira_vez[chave]

                    clientes.sort(key=lambda c: -c["espera_min"])

                    gravar({
                        "status": "ok",
                        # textos lidos de cada conversa (ajuda a configurar os departamentos)
                        "textos_lidos": [i.get("textos") for i in itens][:5],
                        "pendentes": total,
                        "em_atendimento": resultado.get("em_atendimento"),
                        "clientes": clientes,
                    })

                    # guarda uma cópia da página na 1ª vez que houver pendente
                    # (serve para ajustar a leitura, se precisar)
                    if total > 0 and (not salvou_debug or
                                      time.time() - salvou_debug > 300):
                        try:
                            DEBUG_HTML.write_text(page.content(), encoding="utf-8")
                            salvou_debug = time.time()
                        except Exception:
                            pass

                    print(f"{datetime.now():%H:%M:%S}  pendentes={total}  "
                          f"em atendimento={resultado.get('em_atendimento')}")
                    time.sleep(cfg["intervalo"])

        except KeyboardInterrupt:
            print("Encerrado.")
            return
        except Exception as erro:
            texto_erro = str(erro)
            erros_seguidos += 1
            primeira_linha = (texto_erro.strip().splitlines() or [""])[0]
            print(f"⚠ Erro no leitor ({erros_seguidos}x): {primeira_linha}")
            if erros_seguidos <= 1 or erros_seguidos % 10 == 0:
                traceback.print_exc()

            baixo = texto_erro.lower()
            status = "carregando"
            msg = "Reconectando ao Messenger..."
            if ("has been closed" in baixo or "already in use" in baixo
                    or "processsingleton" in baixo or "existing browser session" in baixo):
                # Chrome não abriu: quase sempre perfil preso por um Chrome antigo
                fechar_chrome_orfao()
                if erros_seguidos >= 3:
                    status = "erro"
                    msg = "O Chrome do leitor não abre - feche o Chrome do login e reinicie o painel"
            elif "executable doesn't exist" in baixo:
                status = "erro"
                msg = "Chrome não encontrado - rode o instalar.bat"
            elif erros_seguidos >= 3:
                status = "erro"
                msg = primeira_linha[:140]
            try:
                gravar({"status": status, "mensagem": msg})
            except Exception:
                pass
            time.sleep(10 if erros_seguidos < 3 else 30)


if __name__ == "__main__":
    if "--login" not in sys.argv:
        sys.stdout = Espelho(sys.stdout)
        sys.stderr = Espelho(sys.stderr)
        print("\n" + "=" * 60)
        print(f"{datetime.now():%d/%m/%Y %H:%M:%S}  Leitor iniciado")

    try:
        import playwright  # noqa: F401
    except ImportError:
        print("⚠ O Playwright não está instalado. Rode o instalar.bat.")
        gravar({"status": "erro", "mensagem": "Playwright não instalado - rode o instalar.bat"})
        input("Aperte ENTER para sair...")
        sys.exit(1)

    configuracao = ler_config()

    if not configuracao["ativo"]:
        print("Leitor do Messenger desativado no config.ini (ativo = nao).")
        gravar({"status": "desativado"})
        sys.exit(0)

    if "--login" in sys.argv:
        sys.exit(0 if modo_login(configuracao) else 1)
    else:
        modo_leitor(configuracao)
