from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from presence import views as presence_views

urlpatterns = [
    path("", presence_views.index, name="index"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("api/", include("presence.urls")),
]
