"""Campo de aceite reutilizável.

A regra que sustenta o valor jurídico do checkbox está aqui: `required=True` faz a
recusa acontecer no servidor, e `initial=False` garante que ele nunca chegue
pré-marcado. Nenhum template deve escrever `checked` por conta própria.
"""

from typing import TYPE_CHECKING

from django import forms
from django.urls import reverse
from django.utils.functional import lazy
from django.utils.html import format_html


def _rotulo_aceite():
    # reverse() só resolve com o URLconf carregado, então o label é preguiçoso:
    # o campo é instanciado na importação do módulo do formulário.
    return format_html(
        'Li e aceito os <a href="{}" target="_blank" rel="noopener">Termos de Uso</a> '
        'e a <a href="{}" target="_blank" rel="noopener">Política de Privacidade</a>.',
        reverse("termos"),
        reverse("privacidade"),
    )


rotulo_aceite = lazy(_rotulo_aceite, str)


class AceiteLegalField(forms.BooleanField):
    """Checkbox de aceite: obrigatório no servidor e nunca pré-marcado."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", True)
        kwargs.setdefault("initial", False)
        kwargs.setdefault("label", rotulo_aceite())
        kwargs.setdefault(
            "error_messages",
            {"required": "É preciso aceitar os Termos de Uso e a Política de Privacidade."},
        )
        super().__init__(**kwargs)


class AceiteLegalMixin:
    """Acrescenta o checkbox de aceite a qualquer Form ou ModelForm.

    O campo entra em `__init__`, e não como atributo de classe: campos declarativos
    só são coletados de bases que já são formulários, então um mixin declarativo
    seria silenciosamente ignorado ao ser combinado com um ModelForm.
    """

    campo_aceite = "aceite_legal"

    if TYPE_CHECKING:
        # A classe combinada de verdade (ex.: AceiteForm) é quem traz
        # `fields` em tempo de execução, via forms.Form/forms.BaseForm.
        # Isso só existe para o mypy entender o contrato do mixin.
        fields: dict[str, forms.Field]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields[self.campo_aceite] = AceiteLegalField()


class AceiteForm(AceiteLegalMixin, forms.Form):
    """Formulário mínimo: interstitial de re-aceite e aceite anônimo."""
