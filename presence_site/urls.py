from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from presence import views as presence_views

urlpatterns = [
    path("", presence_views.index, name="index"),
    path("presence/add/", presence_views.add, name="add"),
    path("presence/<str:identifier>/", presence_views.detail, name="detail"),
    path("presence/<str:identifier>/json/", presence_views.detail_json, name="detail_json"),
    path("presence/<str:identifier>/edit/", presence_views.edit, name="edit"),
    path("presence/<str:identifier>/duplicate/", presence_views.duplicate, name="duplicate"),
    path("presence/<str:identifier>/delete/", presence_views.delete, name="delete"),
    path("access-key/", presence_views.access_key_index, name="access_key_index"),
    path("access-key/add/", presence_views.access_key_add, name="access_key_add"),
    path("access-key/<int:pk>/", presence_views.access_key_detail, name="access_key_detail"),
    path("access-key/<int:pk>/edit/", presence_views.access_key_edit, name="access_key_edit"),
    path("access-key/<int:pk>/delete/", presence_views.access_key_delete, name="access_key_delete"),
    path("access-key/<int:pk>/regenerate/", presence_views.access_key_regenerate, name="access_key_regenerate"),
    path("map/", presence_views.world_map, name="map"),
    path("location/", presence_views.location_index, name="location_index"),
    path("location/add/", presence_views.location_add, name="location_add"),
    path("location/<int:pk>/", presence_views.location_detail, name="location_detail"),
    path("location/<int:pk>/edit/", presence_views.location_edit, name="location_edit"),
    path("location/<int:pk>/duplicate/", presence_views.location_duplicate, name="location_duplicate"),
    path("location/<int:pk>/delete/", presence_views.location_delete, name="location_delete"),
    path("login/", presence_views.ThrottledLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("api/", include("presence.urls")),
]
