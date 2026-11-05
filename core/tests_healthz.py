"""Testes da sonda /healthz/ usada pelo smoke-test do deploy."""

from unittest import mock

import pytest
from django.db import OperationalError
from django.test import override_settings

URL = "/healthz/"


@pytest.mark.django_db
def test_healthz_responde_200_com_tudo_no_ar(client):
    resposta = client.get(URL)

    assert resposta.status_code == 200
    assert resposta.content == b"ok\n"
    assert "no-cache" in resposta["Cache-Control"]


@pytest.mark.django_db
def test_healthz_responde_503_quando_o_banco_cai(client, caplog):
    with mock.patch("core.healthz.connection.cursor", side_effect=OperationalError("banco fora")):
        resposta = client.get(URL)

    assert resposta.status_code == 503
    assert resposta.content == b"indisponivel\n"
    # O motivo vai para o log, nunca para o corpo da rota pública.
    assert "banco" in caplog.text
    assert b"banco" not in resposta.content


@pytest.mark.django_db
def test_healthz_responde_503_quando_o_cache_cai(client):
    with mock.patch("core.healthz.cache.set", side_effect=ConnectionError("redis fora")):
        resposta = client.get(URL)

    assert resposta.status_code == 503


@pytest.mark.django_db
def test_healthz_responde_503_quando_o_cache_nao_devolve_o_valor(client):
    with mock.patch("core.healthz.cache.get", return_value=None):
        resposta = client.get(URL)

    assert resposta.status_code == 503


@pytest.mark.django_db
@override_settings(CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL="redis://broker:6379/0")
def test_healthz_responde_503_quando_o_broker_cai(client):
    with mock.patch("config.celery.app.connection_for_write") as conexao:
        conexao.return_value.__enter__.return_value.ensure_connection.side_effect = ConnectionError(
            "broker fora"
        )
        resposta = client.get(URL)

    assert resposta.status_code == 503


@pytest.mark.django_db
@override_settings(CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL="redis://broker:6379/0")
def test_healthz_toca_o_broker_fora_do_modo_eager(client):
    with mock.patch("config.celery.app.connection_for_write") as conexao:
        resposta = client.get(URL)

    assert resposta.status_code == 200
    conexao.return_value.__enter__.return_value.ensure_connection.assert_called_once()


def test_healthz_recusa_post(client):
    assert client.post(URL).status_code == 405
