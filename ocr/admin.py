"""
Admin do app OCR.
"""

from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import OCRJob


@admin.register(OCRJob)
class OCRJobAdmin(ModelAdmin):
    list_display = [
        "id",
        "status",
        "idioma",
        "forcar_ocr",
        "total_input_size_mb",
        "total_output_files",
        "created_at",
        "completed_at",
        "cleaned_up",
    ]
    list_filter = ["status", "idioma", "forcar_ocr", "cleaned_up", "created_at"]
    search_fields = ["session_key", "original_filenames"]
    readonly_fields = [
        "session_key",
        "task_id",
        "original_filenames",
        "total_input_size_mb",
        "idioma",
        "forcar_ocr",
        "status",
        "progress",
        "total_output_files",
        "output_pdf_path",
        "output_md_path",
        "output_pdf_size_mb",
        "output_md_size_mb",
        "total_caracteres",
        "error_message",
        "processing_warnings",
        "created_at",
        "completed_at",
    ]
    ordering = ["-created_at"]
