from django.urls import path

from . import views

app_name = "presence"

urlpatterns = [
    path("presence/<int:pk>/", views.presence_detail, name="detail"),
]
