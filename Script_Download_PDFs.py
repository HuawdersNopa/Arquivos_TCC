import os
import re
from urllib.parse import unquote, urlparse
import requests
import urllib3

# Desativa avisos de SSL para sites governamentais/antigos com certificados expirados
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configurações de entrada e saída
arquivo_links = "Links.txt"
pasta_destino = "PDFs_Downloads"
arquivo_erros = "erros_download.txt"

# Cria a pasta de destino se não existir
os.makedirs(pasta_destino, exist_ok=True)


def limpar_nome_arquivo(nome):
    """Remove caracteres inválidos para nomes de arquivos no Windows/Linux."""
    nome_limpo = re.sub(r'[\\/*?:"<>|]', "_", nome).strip()
    return nome_limpo


def obter_nome_original(url, response):
    """Tenta extrair o nome original pelo cabeçalho HTTP ou pela própria URL."""
    nome = ""
    content_disp = response.headers.get("Content-Disposition", "")

    # 1. Tenta pegar pelo padrão UTF-8 do Content-Disposition (filename*=UTF-8''nome.pdf)
    match_utf8 = re.findall(
        r"filename\*=UTF-8''([^;]+)", content_disp, flags=re.IGNORECASE
    )
    if match_utf8:
        nome = unquote(match_utf8[0])
    else:
        # 2. Tenta pegar pelo padrão comum (filename="nome.pdf" ou filename=nome.pdf)
        match_comum = re.findall(
            r'filename="?([^";]+)"?', content_disp, flags=re.IGNORECASE
        )
        if match_comum:
            nome = unquote(match_comum[0])

    # 3. Se não veio no cabeçalho, extrai da URL
    if not nome:
        caminho = urlparse(url).path
        partes = [p for p in caminho.split("/") if p]

        # Ignora sufixos genéricos comuns em portais (ex: /file, /download, /@@display-file/file)
        if partes:
            if partes[-1].lower() in ["file", "download"] and len(partes) > 1:
                if (
                    partes[-2].lower() == "@@display-file"
                    and len(partes) > 2
                ):
                    nome = unquote(partes[-3])
                else:
                    nome = unquote(partes[-2])
            else:
                nome = unquote(partes[-1])

    # 4. Fallback caso o link seja apenas um ID numérico ou vazio
    if not nome or nome == "/":
        nome = "documento_sem_nome.pdf"

    # Garante que termina com .pdf
    if not nome.lower().endswith(".pdf"):
        nome += ".pdf"

    return limpar_nome_arquivo(nome)


# Lê o arquivo Links_2.txt ignorando linhas vazias e comentários (#Pagina X)
if not os.path.exists(arquivo_links):
    print(f"ERRO: O arquivo '{arquivo_links}' não foi encontrado na raiz.")
    exit(1)

links = []
with open(arquivo_links, "r", encoding="utf-8") as f:
    for linha in f:
        linha = linha.strip()
        if linha and not linha.startswith("#"):
            links.append(linha)

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
}

erros = []
total_links = len(links)
digitos = len(str(total_links))  # Ex: 3 dígitos se houver 100+ links (001, 002...)

print(f"Lidos {total_links} links de '{arquivo_links}'. Iniciando downloads...\n")

for indice, url in enumerate(links, start=1):
    numero_formatado = str(indice).zfill(digitos)
    print(f"[{numero_formatado}/{total_links}] Baixando: {url}")

    try:
        # verify=False evita falhas em sites de prefeituras/órgãos com SSL desatualizado
        resposta = requests.get(
            url,
            headers=headers,
            stream=True,
            timeout=20,
            allow_redirects=True,
            verify=False,
        )
        resposta.raise_for_status()

        # Lê o início do arquivo para validar se é realmente um PDF
        content_type = resposta.headers.get("Content-Type", "").lower()
        primeiros_bytes = next(resposta.iter_content(chunk_size=1024), b"")

        # Verifica assinatura mágica '%PDF-' ou Content-Type de PDF/binário
        eh_pdf = (
            b"%PDF-" in primeiros_bytes[:1024]
            or "application/pdf" in content_type
        )

        if not eh_pdf:
            motivo = (
                "O servidor não retornou um PDF válido (Content-Type:"
                f" '{content_type}'). Provável página HTML ou erro do site."
            )
            print(f"   -> FALHA: {motivo}")
            erros.append(f"[{numero_formatado}] {url}\n    Motivo: {motivo}\n")
            continue

        nome_original = obter_nome_original(url, resposta)
        nome_final = f"{numero_formatado}_{nome_original}"
        caminho_completo = os.path.join(pasta_destino, nome_final)

        with open(caminho_completo, "wb") as arquivo_pdf:
            arquivo_pdf.write(primeiros_bytes)
            for chunk in resposta.iter_content(chunk_size=8192):
                if chunk:
                    arquivo_pdf.write(chunk)

        print(f"   -> OK: {nome_final}")

    except requests.exceptions.HTTPError as e:
        motivo = f"Erro HTTP {e.response.status_code} ({e.response.reason})"
        print(f"   -> FALHA: {motivo}")
        erros.append(f"[{numero_formatado}] {url}\n    Motivo: {motivo}\n")

    except requests.exceptions.ConnectionError:
        motivo = (
            "Erro de conexão (domínio inexistente, site fora do ar ou conexão"
            " recusada)"
        )
        print(f"   -> FALHA: {motivo}")
        erros.append(f"[{numero_formatado}] {url}\n    Motivo: {motivo}\n")

    except requests.exceptions.Timeout:
        motivo = (
            "Timeout (o servidor demorou mais de 20 segundos para responder)"
        )
        print(f"   -> FALHA: {motivo}")
        erros.append(f"[{numero_formatado}] {url}\n    Motivo: {motivo}\n")

    except Exception as e:
        motivo = f"Erro inesperado: {str(e)}"
        print(f"   -> FALHA: {motivo}")
        erros.append(f"[{numero_formatado}] {url}\n    Motivo: {motivo}\n")

# Salva o relatório de erros na raiz, se houver falhas
if erros:
    with open(arquivo_erros, "w", encoding="utf-8") as f:
        f.write(
            f"RELATÓRIO DE FALHAS NO DOWNLOAD ({len(erros)} de"
            f" {total_links} links falharam)\n"
        )
        f.write("=" * 65 + "\n\n")
        f.writelines(erros)
    print(
        f"\nFinalizado! {total_links - len(erros)} baixados com sucesso."
        f" {len(erros)} falhas registradas em '{arquivo_erros}'."
    )
else:
    print(
        f"\nFinalizado! Todos os {total_links} PDFs foram baixados na pasta"
        f" '{pasta_destino}'."
    )