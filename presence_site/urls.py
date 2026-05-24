from django.contrib import admin
from django.urls import include, path

from presence import views as presence_views

urlpatterns = [
    path("", presence_views.index, name="index"),
    path("admin/", admin.site.urls),
    path("api/", include("presence.urls")),
]
