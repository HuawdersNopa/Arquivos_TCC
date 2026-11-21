# -*- coding: utf-8 -*-
"""
Script para baixar PDFs a partir de uma lista de links.
Formato de saída: <Nº>_<nome_original>.pdf
Pasta de destino: PDFs_Downloads
Erros registrados em: erros_download.txt
"""

import os
import re
import sys
import time
import requests
from urllib.parse import urlparse, unquote

# ─── Configurações ───────────────────────────────────────────────────────────
ARQUIVO_LINKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Links.txt")
PASTA_DOWNLOADS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PDFs_Downloads")
ARQUIVO_ERROS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "erros_download.txt")

TIMEOUT_CONEXAO = 30        # segundos para conectar
TIMEOUT_LEITURA = 120       # segundos para receber o corpo
MAX_TENTATIVAS = 3           # tentativas por link
ESPERA_ENTRE_TENTATIVAS = 5  # segundos entre retentativas

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
}


def sanitizar_nome(nome: str) -> str:
    """Remove caracteres inválidos para nomes de arquivo no Windows."""
    # Substituir caracteres proibidos por underscore
    nome = re.sub(r'[<>:"/\\|?*]', '_', nome)
    # Remover espaços extras e pontos no final
    nome = nome.strip().rstrip('.')
    # Limitar tamanho (Windows tem limite de 255 caracteres no nome)
    if len(nome) > 200:
        nome = nome[:200]
    return nome


def extrair_nome_do_url(url: str) -> str:
    """Extrai o nome original do arquivo a partir da URL."""
    parsed = urlparse(url)
    caminho = unquote(parsed.path)

    # Pegar o último segmento do caminho
    nome = os.path.basename(caminho)

    # Se não tem extensão .pdf ou está vazio, tentar alternativas
    if not nome or nome == "/" or '.' not in nome:
        # Usar os últimos segmentos do caminho como nome
        partes = [p for p in caminho.split('/') if p]
        if partes:
            nome = partes[-1]
        else:
            # Usar o domínio + path hash como fallback
            nome = parsed.netloc.replace('.', '_')

    return nome


def extrair_nome_do_header(response, url: str) -> str:
    """Tenta extrair o nome do arquivo do header Content-Disposition."""
    cd = response.headers.get("Content-Disposition", "")
    if cd:
        # Tentar filename*= (RFC 5987) primeiro
        match = re.search(r"filename\*\s*=\s*(?:UTF-8''|utf-8'')(.+)", cd, re.IGNORECASE)
        if match:
            return unquote(match.group(1).strip().strip('"'))
        # Depois tentar filename=
        match = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.IGNORECASE)
        if match:
            return unquote(match.group(1).strip().strip('"'))

    return extrair_nome_do_url(url)


def garantir_extensao_pdf(nome: str) -> str:
    """Garante que o nome termine com .pdf."""
    if not nome.lower().endswith('.pdf'):
        nome += '.pdf'
    return nome


def ler_links(caminho: str) -> list[tuple[int, str]]:
    """Lê o arquivo de links e retorna lista de (número, url)."""
    links = []
    numero = 0

    with open(caminho, 'r', encoding='utf-8') as f:
        for linha in f:
            linha = linha.strip()
            # Ignorar linhas vazias e comentários (#Pagina X)
            if not linha or linha.startswith('#'):
                continue
            numero += 1
            links.append((numero, linha))

    return links


def baixar_pdf(numero: int, url: str, pasta: str) -> tuple[bool, str]:
    """
    Baixa um PDF da URL fornecida.
    Retorna (sucesso: bool, mensagem: str).
    Usa streaming para garantir download completo.
    """
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            # Fazer request com streaming para controlar o download
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=(TIMEOUT_CONEXAO, TIMEOUT_LEITURA),
                stream=True,
                allow_redirects=True,
                verify=True,
            )

            # Verificar status HTTP
            if response.status_code != 200:
                msg = f"HTTP {response.status_code} - {response.reason}"
                if tentativa < MAX_TENTATIVAS:
                    time.sleep(ESPERA_ENTRE_TENTATIVAS)
                    continue
                return False, msg

            # Verificar se o Content-Type parece ser PDF ou é genérico (aceitar ambos)
            content_type = response.headers.get("Content-Type", "").lower()
            tipos_aceitos = [
                "application/pdf",
                "application/octet-stream",
                "application/force-download",
                "application/x-pdf",
                "binary/octet-stream",
                "application/download",
            ]
            tipo_ok = any(t in content_type for t in tipos_aceitos) or not content_type
            # Mesmo que não seja um tipo reconhecido, vamos tentar baixar
            # (muitos servidores retornam text/html mas entregam PDF)

            # Obter nome do arquivo
            nome_original = extrair_nome_do_header(response, url)
            nome_original = sanitizar_nome(nome_original)
            nome_original = garantir_extensao_pdf(nome_original)

            # Montar nome final: Nº_nome_original.pdf
            nome_final = f"{numero}_{nome_original}"
            caminho_final = os.path.join(pasta, nome_final)

            # Verificar tamanho esperado (se disponível)
            tamanho_esperado = response.headers.get("Content-Length")
            if tamanho_esperado:
                tamanho_esperado = int(tamanho_esperado)

            # Baixar para arquivo temporário primeiro, depois renomear
            caminho_temp = caminho_final + ".tmp"
            tamanho_baixado = 0

            with open(caminho_temp, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        tamanho_baixado += len(chunk)

            # Verificar integridade
            if tamanho_esperado and tamanho_baixado != tamanho_esperado:
                os.remove(caminho_temp)
                msg = (
                    f"Download incompleto: recebido {tamanho_baixado} bytes "
                    f"de {tamanho_esperado} esperados"
                )
                if tentativa < MAX_TENTATIVAS:
                    time.sleep(ESPERA_ENTRE_TENTATIVAS)
                    continue
                return False, msg

            if tamanho_baixado == 0:
                os.remove(caminho_temp)
                msg = "Arquivo vazio (0 bytes recebidos)"
                if tentativa < MAX_TENTATIVAS:
                    time.sleep(ESPERA_ENTRE_TENTATIVAS)
                    continue
                return False, msg

            # Verificar se o conteúdo começa com assinatura PDF (%PDF)
            with open(caminho_temp, 'rb') as f:
                cabecalho = f.read(8)

            if not cabecalho.startswith(b'%PDF'):
                os.remove(caminho_temp)
                # Tentar decodificar início para mensagem de erro
                try:
                    inicio_texto = cabecalho.decode('utf-8', errors='replace')
                except Exception:
                    inicio_texto = str(cabecalho)
                msg = (
                    f"Conteúdo não é PDF válido (início: {inicio_texto!r}). "
                    f"Content-Type: {content_type}"
                )
                return False, msg

            # Tudo OK — renomear de .tmp para definitivo
            if os.path.exists(caminho_final):
                os.remove(caminho_final)
            os.rename(caminho_temp, caminho_final)

            tamanho_mb = tamanho_baixado / (1024 * 1024)
            return True, f"OK ({tamanho_mb:.2f} MB) → {nome_final}"

        except requests.exceptions.SSLError as e:
            msg = f"Erro SSL: {e}"
            # Para erros SSL, tentar sem verificação na última tentativa
            if tentativa == MAX_TENTATIVAS:
                try:
                    return _baixar_sem_ssl(numero, url, pasta)
                except Exception as e2:
                    return False, f"Erro SSL (mesmo sem verificação): {e2}"
            time.sleep(ESPERA_ENTRE_TENTATIVAS)

        except requests.exceptions.ConnectionError as e:
            msg = f"Erro de conexão: {e}"
            if tentativa < MAX_TENTATIVAS:
                time.sleep(ESPERA_ENTRE_TENTATIVAS)
                continue
            return False, msg

        except requests.exceptions.Timeout as e:
            msg = f"Timeout: {e}"
            if tentativa < MAX_TENTATIVAS:
                time.sleep(ESPERA_ENTRE_TENTATIVAS)
                continue
            return False, msg

        except requests.exceptions.RequestException as e:
            msg = f"Erro de request: {e}"
            if tentativa < MAX_TENTATIVAS:
                time.sleep(ESPERA_ENTRE_TENTATIVAS)
                continue
            return False, msg

        except Exception as e:
            msg = f"Erro inesperado: {type(e).__name__}: {e}"
            if tentativa < MAX_TENTATIVAS:
                time.sleep(ESPERA_ENTRE_TENTATIVAS)
                continue
            return False, msg

    return False, msg


def _baixar_sem_ssl(numero: int, url: str, pasta: str) -> tuple[bool, str]:
    """Fallback: tenta baixar ignorando certificado SSL."""
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=(TIMEOUT_CONEXAO, TIMEOUT_LEITURA),
        stream=True,
        allow_redirects=True,
        verify=False,
    )

    if response.status_code != 200:
        return False, f"HTTP {response.status_code} (fallback sem SSL)"

    nome_original = extrair_nome_do_header(response, url)
    nome_original = sanitizar_nome(nome_original)
    nome_original = garantir_extensao_pdf(nome_original)
    nome_final = f"{numero}_{nome_original}"
    caminho_final = os.path.join(pasta, nome_final)
    caminho_temp = caminho_final + ".tmp"

    tamanho_baixado = 0
    with open(caminho_temp, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                tamanho_baixado += len(chunk)

    if tamanho_baixado == 0:
        os.remove(caminho_temp)
        return False, "Arquivo vazio (fallback sem SSL)"

    with open(caminho_temp, 'rb') as f:
        cabecalho = f.read(8)

    if not cabecalho.startswith(b'%PDF'):
        os.remove(caminho_temp)
        return False, f"Conteúdo não é PDF (fallback sem SSL)"

    if os.path.exists(caminho_final):
        os.remove(caminho_final)
    os.rename(caminho_temp, caminho_final)

    tamanho_mb = tamanho_baixado / (1024 * 1024)
    return True, f"OK ({tamanho_mb:.2f} MB, SSL ignorado) → {nome_final}"


def main():
    print("=" * 70)
    print("  DOWNLOAD DE PDFs - Lista de Links")
    print("=" * 70)

    # Verificar arquivo de links
    if not os.path.exists(ARQUIVO_LINKS):
        print(f"\n❌ Arquivo de links não encontrado: {ARQUIVO_LINKS}")
        sys.exit(1)

    # Ler links
    links = ler_links(ARQUIVO_LINKS)
    total = len(links)
    print(f"\n📋 {total} links encontrados no arquivo.\n")

    # Criar pasta de downloads
    os.makedirs(PASTA_DOWNLOADS, exist_ok=True)

    # Listas para resultado
    sucessos = []
    erros = []

    # Processar cada link
    for i, (numero, url) in enumerate(links, 1):
        print(f"[{i}/{total}] #{numero}: ", end="", flush=True)

        # Mostrar URL resumida
        url_curta = url[:80] + "..." if len(url) > 80 else url
        print(url_curta)

        sucesso, mensagem = baixar_pdf(numero, url, PASTA_DOWNLOADS)

        if sucesso:
            print(f"    ✅ {mensagem}")
            sucessos.append((numero, url, mensagem))
        else:
            print(f"    ❌ {mensagem}")
            erros.append((numero, url, mensagem))

        # Pequena pausa entre downloads para não sobrecarregar
        if i < total:
            time.sleep(0.5)

    # ─── Relatório Final ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  RELATÓRIO FINAL")
    print("=" * 70)
    print(f"  ✅ Baixados com sucesso: {len(sucessos)}/{total}")
    print(f"  ❌ Falhas:               {len(erros)}/{total}")
    print(f"  📁 Pasta:                {PASTA_DOWNLOADS}")

    # Salvar erros em arquivo
    if erros:
        with open(ARQUIVO_ERROS, 'w', encoding='utf-8') as f:
            f.write("RELATÓRIO DE ERROS DE DOWNLOAD\n")
            f.write(f"Data: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Total de erros: {len(erros)}/{total}\n")
            f.write("=" * 80 + "\n\n")
            for numero, url, motivo in erros:
                f.write(f"Link #{numero}\n")
                f.write(f"  URL:   {url}\n")
                f.write(f"  Motivo: {motivo}\n")
                f.write("-" * 80 + "\n")

        print(f"  📄 Erros salvos em:      {ARQUIVO_ERROS}")
    else:
        print("  🎉 Nenhum erro!")

    print("=" * 70)


if __name__ == "__main__":
    main()
