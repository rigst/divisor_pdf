"""
URL configuration for the OCR app.
"""

from django.urls import path

from . import views

app_name = "ocr"

urlpatterns = [
    path("", views.index, name="index"),
    path("api/upload/", views.upload, name="upload"),
    path("api/status/<int:job_id>/", views.status, name="status"),
    path("api/download/<int:job_id>/<str:formato>/", views.download, name="download"),
]
