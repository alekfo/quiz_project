from django.contrib import admin

from .models import GenerationRequest

@admin.register(GenerationRequest)
class GenerationRequestAdmin(admin.ModelAdmin):

    list_display = "pk", "title", "subject", "category", "questions", "level", "audience", "style", "status", "result"
    ordering = ("pk",)

    def get_queryset(self, request):
        return  GenerationRequest.objects.select_related("user", "category")