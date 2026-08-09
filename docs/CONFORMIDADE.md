# Conformidade legal — LGPD e Marco Civil

Como o Divisor registra o aceite dos termos **sem ter contas de usuário**, por quanto
tempo guarda os registros de acesso e o que fazer para publicar uma versão nova das
políticas.

## 1. Registro de aceite anônimo

Este é o único dos sistemas Stölben sem cadastro: o aceite não pode ser vinculado a uma
conta, então é vinculado à **sessão**.

**Onde acontece.** O checkbox aparece no formulário de upload, desmarcado, junto com os
demais controles. Enquanto não for marcado, o botão de envio fica desabilitado — mas isso
é conveniência. Quem recusa o envio é o servidor, em `splitter/views.py:upload()`:

```python
if not aceite_anonimo_valido(request):
    if request.POST.get("aceite_legal") not in ("true", "1", "on"):
        return JsonResponse({"error": ...}, status=400)
    registrar_aceite(request, origem=OrigemAceite.UPLOAD_ANONIMO)
```

Sem essa checagem no servidor, bastaria o inspetor do navegador para burlar o checkbox.

**O que fica gravado.** Um `AceiteLegal` por documento vigente, com `usuario=None` e:
`session_key`, `ip`, `user_agent`, `aceito_em`, `origem`, o `sha256` do texto naquele
momento e um JSON de evidência (host, path, método, `Referer`, `X-Forwarded-For` bruto,
idioma e as versões vigentes na hora).

**Uma vez por sessão.** Depois do primeiro aceite, o checkbox some da página e os envios
seguintes não pedem nada — a prova daquela sessão já está gravada. Como
`SESSION_COOKIE_AGE` é de 1 hora, uma visita no dia seguinte aceita de novo, o que é o
comportamento correto.

**Versão nova volta a pedir.** O aceite guardado na sessão registra *quais* versões foram
aceitas. Publicada uma versão nova, `aceite_anonimo_valido()` passa a devolver `False` e
o próximo envio exige aceite outra vez.

## 2. O que este projeto NÃO usa do app `legal`

O app é copiado igual entre os sistemas, mas aqui duas peças ficam de fora, porque
dependem de usuário autenticado:

- **`AceiteObrigatorioMiddleware`** não entra no `MIDDLEWARE` — ele existe para forçar
  re-aceite de quem tem conta.
- **`/legal/reaceite/` e `/legal/meus-aceites/`** não são publicadas em `legal/urls.py`.
  As views seguem no módulo para a cópia do app não divergir das dos outros projetos.

## 3. Publicar uma versão nova das políticas

O **banco é a fonte da verdade**; `legal/documentos/<tipo>/<versao>.md` é o espelho em git.

1. No admin, em *Documentos legais*, selecione a versão vigente e rode
   **"Duplicar como nova versão (rascunho)"**.
2. Edite o rascunho em Markdown; o campo *Pré-visualização* mostra o resultado sanitizado.
3. Marque **mudança material** se quiser que todos aceitem de novo.
4. Selecione o rascunho e rode **"Publicar rascunhos selecionados"**.
5. Espelhe em git:
   ```bash
   ./venv/bin/python manage.py exportar_documentos_legais
   git add legal/documentos && git commit -m "Publica <documento> vX.Y"
   ```

Versão publicada não é editável nem apagável pelo admin, nem antes do primeiro aceite —
no instante em que vai ao ar já está sendo exibida. Para mudar o texto, publique outra.

`importar_documentos_legais` faz o caminho inverso e **recusa** sobrescrever versão
existente cujo texto tenha mudado, o que impede alterar retroativamente algo já aceito.

## 4. Extrair evidência

No admin, em *Conformidade legal → Aceites*: filtre e use **"Exportar seleção em CSV"**.
O CSV traz o hash gravado no aceite e o hash atual do documento lado a lado, mais a
coluna `integro` — se divergirem, o texto foi alterado depois do aceite.

`AceiteLegal` é somente leitura: não há como adicionar, editar ou apagar pelo admin.

## 5. Guarda dos registros de acesso (6 meses)

O art. 15 do Marco Civil da Internet exige 6 meses. Quem cumpre é o nginx.

O server block deste site já grava em `/var/log/nginx/acesso/divisor.access.log`, e a
rotação de 200 dias está em `/etc/logrotate.d/stolben-acesso`. Para reinstalar ou
replicar:

```bash
sudo install -d -o root -g adm -m 0755 /var/log/nginx/acesso
sudo cp deploy/logrotate/stolben-acesso /etc/logrotate.d/stolben-acesso
sudo python3 deploy/nginx_acesso.py --dry-run   # simulação
sudo python3 deploy/nginx_acesso.py && sudo nginx -t && sudo systemctl reload nginx
```

O subdiretório `acesso/` evita colidir com o `/etc/logrotate.d/nginx` do sistema, que
rotaciona `/var/log/nginx/*.log` a cada 14 dias — o glob não é recursivo.

O `X-Forwarded-For` é lido pelo **último** item, em `legal/utils.py:ip_do_request()`:
atrás do nginx, esse é o IP que ele observou; os anteriores vieram do cliente e são
forjáveis.

## 6. Checklist de deploy

```bash
./venv/bin/python manage.py migrate
./venv/bin/python manage.py importar_documentos_legais --publicar   # só na 1ª vez
./venv/bin/python manage.py collectstatic --noinput                 # unfold traz estáticos
sudo systemctl reload divisor_pdf.service
```

`collectstatic` precisa das variáveis de produção: o app usa
`ManifestStaticFilesStorage`, e um estático fora do manifesto derruba a página com 500.
