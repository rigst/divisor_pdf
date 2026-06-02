"""
Admin do app Splitter.
"""

from django.contrib import admin
from .models import SplitJob


@admin.register(SplitJob)
class SplitJobAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'status', 'total_input_size_mb', 'max_size_mb',
        'total_output_files', 'created_at', 'completed_at', 'cleaned_up'
    ]
    list_filter = ['status', 'cleaned_up', 'created_at']
    search_fields = ['session_key', 'original_filenames']
    readonly_fields = [
        'session_key', 'task_id', 'original_filenames',
        'total_input_size_mb', 'total_output_files',
        'output_zip_path', 'error_message', 'created_at', 'completed_at'
    ]
    ordering = ['-created_at']
