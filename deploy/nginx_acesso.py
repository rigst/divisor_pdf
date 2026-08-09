#!/usr/bin/env python3
"""Aponta o access_log de cada site para /var/log/nginx/acesso/<app>.access.log.

Só mexe no server block que contém `listen 443` — o bloco de redirecionamento
80->443 não precisa registrar acesso, já que nada é servido por ele. Se o bloco
já tiver um access_log próprio, o caminho é trocado; se não, a diretiva é
inserida logo abaixo do server_name.

Uso:
    sudo python3 nginx_acesso.py --dry-run   # mostra o diff, não grava
    sudo python3 nginx_acesso.py             # grava e faz backup .bak
"""

import argparse
import shutil
import sys
from pathlib import Path

SITES = Path("/etc/nginx/sites-enabled")
DESTINO = "/var/log/nginx/acesso"

# arquivo em sites-enabled -> nome do log
APPS = {
    "sistema_trilhas": "trilhas",
    "sistema_questoes": "questoes",
    "sistema_orcamentos": "orcamentos",
    "sistema_vetorial": "vetorial",
    "divisor_pdf": "divisor",
    "site_stolben": "site",
}


def bloco_443(linhas):
    """(inicio, fim) do server block que contém `listen 443`, por contagem de chaves."""
    inicio = None
    profundidade = 0
    for i, linha in enumerate(linhas):
        if inicio is None:
            if linha.strip().startswith("server") and "{" in linha:
                inicio = i
                profundidade = linha.count("{") - linha.count("}")
            continue
        profundidade += linha.count("{") - linha.count("}")
        if profundidade <= 0:
            trecho = linhas[inicio : i + 1]
            if any("listen 443" in l for l in trecho):
                return inicio, i
            inicio = None
    return None, None


def nivel_do_bloco(linhas, inicio, fim):
    """Índices das linhas que estão no nível do server, fora de qualquer location."""
    indices = []
    profundidade = 0
    for i in range(inicio + 1, fim):
        linha = linhas[i]
        if profundidade == 0:
            indices.append(i)
        profundidade += linha.count("{") - linha.count("}")
    return indices


def ajustar(caminho, nome, dry_run):
    linhas = caminho.read_text(encoding="utf-8").splitlines(keepends=True)
    inicio, fim = bloco_443(linhas)
    if inicio is None:
        print(f"  !! {caminho.name}: nenhum server block com 'listen 443'")
        return False

    alvo = f"    access_log {DESTINO}/{nome}.access.log;\n"
    proprios = nivel_do_bloco(linhas, inicio, fim)

    for i in proprios:
        if linhas[i].strip().startswith("access_log"):
            if linhas[i] == alvo:
                print(f"  == {caminho.name}: já aponta para {DESTINO}")
                return False
            print(f"  -> {caminho.name}: troca {linhas[i].strip()}")
            linhas[i] = alvo
            break
    else:
        for i in proprios:
            if linhas[i].strip().startswith("server_name"):
                print(f"  ++ {caminho.name}: insere após {linhas[i].strip()}")
                linhas.insert(i + 1, alvo)
                break
        else:
            print(f"  !! {caminho.name}: server_name não encontrado no bloco 443")
            return False

    if dry_run:
        return True

    shutil.copy2(caminho, caminho.with_suffix(caminho.suffix + ".bak"))
    caminho.write_text("".join(linhas), encoding="utf-8")
    criar_log(nome)
    return True


def criar_log(nome):
    """Cria o arquivo de log já com dono e permissão finais.

    O nginx roda como root e criaria o arquivo 0644 root:root, deixando os IPs
    legíveis por qualquer usuário do servidor até a primeira rotação corrigir.
    Criar antes fecha essa janela.
    """
    import grp
    import os
    import pwd

    destino = Path(DESTINO) / f"{nome}.access.log"
    if destino.exists():
        return
    destino.touch()
    os.chown(destino, pwd.getpwnam("www-data").pw_uid, grp.getgrnam("adm").gr_gid)
    destino.chmod(0o640)
    print(f"     log criado: {destino} (0640 www-data:adm)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.dry_run and not Path("/var/log/nginx/acesso").is_dir():
        print("Crie antes: sudo install -d -o root -g adm -m 0755 /var/log/nginx/acesso")
        return 1

    mudou = 0
    for arquivo, nome in APPS.items():
        caminho = SITES / arquivo
        if not caminho.exists():
            print(f"  ?? {arquivo}: não existe em {SITES}")
            continue
        # sites-enabled costuma ser link simbólico; editar o arquivo real.
        caminho = caminho.resolve()
        if ajustar(caminho, nome, args.dry_run):
            mudou += 1

    print(f"\n{mudou} arquivo(s) {'a alterar' if args.dry_run else 'alterado(s)'}.")
    if not args.dry_run and mudou:
        print("Agora: sudo nginx -t && sudo systemctl reload nginx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
