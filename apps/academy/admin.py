from django.contrib import admin

from apps.academy.models import Batch, Student, StudentDocument, StudentStatusHistory


class StudentStatusHistoryInline(admin.TabularInline):
    model = StudentStatusHistory
    extra = 0
    # History is append-only: it records what happened, so it is never edited.
    readonly_fields = ["from_status", "to_status", "from_stage", "to_stage", "reason", "changed_by", "is_automated", "created_at"]
    can_delete = False
    ordering = ["-created_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "instructor", "status", "start_date", "end_date", "research_days", "student_total"]
    list_filter = ["status"]
    search_fields = ["code", "name"]
    readonly_fields = ["created_at", "updated_at"]

    @admin.display(description="Students")
    def student_total(self, obj):
        return obj.students.filter(deleted_at__isnull=True).count()


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ["enrollment_id", "full_name", "batch", "status", "roadmap_stage", "research_deadline", "instructor"]
    list_filter = ["status", "roadmap_stage", "batch", "country"]
    search_fields = ["enrollment_id", "full_name", "email", "phone", "lms_student_id"]
    # Generated once and the key the whole system references.
    readonly_fields = ["enrollment_id", "created_at", "updated_at", "last_activity_at"]
    inlines = [StudentStatusHistoryInline]
    date_hierarchy = "enrollment_date"

    fieldsets = (
        ("Identity", {"fields": ("enrollment_id", "full_name", "guardian_name", "email", "phone", "city", "country", "user")}),
        ("Enrollment", {"fields": ("batch", "instructor", "enrollment_date", "course_start_date", "expected_completion_date")}),
        ("Research", {"fields": ("research_start_date", "research_deadline")}),
        ("Progress", {"fields": ("status", "roadmap_stage", "agreement_status", "completion_status", "last_activity_at")}),
        ("Other", {"fields": ("lms_student_id", "profile_image_url", "notes", "created_at", "updated_at", "deleted_at")}),
    )

    def get_queryset(self, request):
        # Archived students stay reachable in the admin; soft delete is not
        # meant to hide records from an administrator.
        return Student.all_objects.all()


@admin.register(StudentDocument)
class StudentDocumentAdmin(admin.ModelAdmin):
    list_display = ["file_name", "student", "type", "uploaded_by", "created_at"]
    list_filter = ["type"]
    search_fields = ["file_name", "student__full_name", "student__enrollment_id"]


@admin.register(StudentStatusHistory)
class StudentStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ["student", "from_status", "to_status", "changed_by", "is_automated", "created_at"]
    list_filter = ["to_status", "is_automated"]
    search_fields = ["student__full_name", "student__enrollment_id"]
    readonly_fields = [f.name for f in StudentStatusHistory._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
