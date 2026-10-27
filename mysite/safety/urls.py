from django.urls import path

from .views import HealthProfileView

urlpatterns = [
    path('user/profile/', HealthProfileView.as_view()),
]
