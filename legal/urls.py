from django.urls import path

from . import views

app_name = "legal"

# O divisor não tem contas: o aceite é anônimo, preso à sessão. As rotas de
# re-aceite e de "meus aceites" dependem de usuário autenticado e por isso não
# são publicadas aqui — as views seguem no módulo para manter esta cópia do app
# igual à dos outros projetos.
urlpatterns = [
    path("<str:tipo>/<str:versao>/", views.versao, name="versao"),
]
